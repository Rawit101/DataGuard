from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb
from fastapi import UploadFile
from pydantic import BaseModel, Field

from .config import MAX_UPLOAD_BYTES, MEMORY_LIMIT, managed_path
from .db import connect, new_id, now, one
from .serialization import json_value


class ImportOptions(BaseModel):
    header: bool = True
    delimiter: str = Field(default=",", min_length=1, max_length=1, pattern=r"^[^\r\n]$")
    quotechar: str = Field(default='"', min_length=1, max_length=1)
    escapechar: str | None = Field(default=None, min_length=1, max_length=1)
    encoding: str = "utf-8-sig"
    null_tokens: list[str] = Field(default_factory=lambda: [""])

    def accepted_encoding(self) -> str:
        if self.encoding not in {"utf-8", "utf-8-sig", "utf-16", "latin-1"}:
            raise ValueError("Supported encodings: UTF-8, UTF-8 BOM, UTF-16, Latin-1")
        return self.encoding


def safe_alias(value: str) -> str:
    value = re.sub(r"[^a-z0-9_]", "_", value.lower().strip())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value or value[0].isdigit():
        value = "dataset_" + value
    if value.startswith("__"):
        value = "dataset" + value
    return value[:60]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def infer_type(values: list[str | None]) -> str:
    present = [v for v in values if v is not None and v != ""]
    if not present:
        return "VARCHAR"
    for typ, checker in (
        ("BIGINT", lambda x: re.fullmatch(r"[+-]?\d+", x) is not None),
        ("DOUBLE", lambda x: bool(Decimal(x).is_finite())),
        ("DATE", lambda x: bool(date.fromisoformat(x))),
    ):
        try:
            if all(checker(v) for v in present):
                return typ
        except (ValueError, InvalidOperation):
            pass
    return "VARCHAR"


def type_valid(value: str | None, typ: str) -> bool:
    if value is None:
        return True
    try:
        if typ == "BIGINT":
            return (re.fullmatch(r"[+-]?\d+", value) is not None
                    and -(2**63) <= int(value) < 2**63)
        if typ in {"DOUBLE", "DECIMAL"}:
            return Decimal(value).is_finite()
        if typ == "DATE":
            date.fromisoformat(value)
        if typ == "TIMESTAMP":
            datetime.fromisoformat(value)
        if typ == "BOOLEAN":
            return value.strip().lower() in {"true", "false", "t", "f", "yes", "no", "y", "n", "1", "0"}
        return True
    except (ValueError, InvalidOperation):
        return False


def csv_rows(path: Path, opts: ImportOptions):
    with path.open("r", encoding=opts.accepted_encoding(), newline="") as f:
        reader = csv.reader(f, delimiter=opts.delimiter, quotechar=opts.quotechar,
                            escapechar=opts.escapechar, strict=True)
        columns: list[str] | None = None
        record = 0
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except (csv.Error, UnicodeError) as exc:
                start_line = last_line + 1 if 'last_line' in locals() else 1
                with path.open("r", encoding=opts.accepted_encoding(), errors="replace") as source:
                    # A failed CSV reader cannot reliably recover record boundaries. Record
                    # the entire unread tail as rejected, while the original file retains
                    # its complete bytes for later inspection or reimport.
                    raw = "".join(line for line_number, line in enumerate(source, 1)
                                  if line_number >= start_line)
                yield ("reject", start_line, None, raw,
                       f"CSV parse error from physical line {start_line}; remaining input rejected because record boundaries are uncertain: {exc}")
                break
            last_line = reader.line_num
            if columns is None:
                if opts.header:
                    columns = [x.strip() or f"column_{i+1}" for i, x in enumerate(row)]
                    normalized = [column.casefold() for column in columns]
                    if len(set(normalized)) != len(columns) or "__source_row_id" in normalized:
                        raise ValueError("CSV header has duplicate or reserved column names")
                    yield ("header", columns)
                    continue
                columns = [f"column_{i+1}" for i in range(len(row))]
                yield ("header", columns)
            record += 1
            if len(row) != len(columns):
                yield ("reject", reader.line_num, None, json.dumps(row, ensure_ascii=False),
                       f"Expected {len(columns)} columns, found {len(row)}")
            else:
                values = [None if x in opts.null_tokens else x for x in row]
                yield ("row", record, values)


async def stage_upload(upload: UploadFile, options: ImportOptions) -> dict:
    if not upload.filename or Path(upload.filename).suffix.lower() not in {".csv", ".parquet"}:
        raise ValueError("Choose a CSV or Parquet file")
    token = new_id()
    suffix = Path(upload.filename).suffix.lower()
    path = managed_path("tmp", token + suffix)
    size = 0
    sha = hashlib.sha256()
    try:
        with path.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError(f"File exceeds {MAX_UPLOAD_BYTES} byte upload limit")
                out.write(chunk)
                sha.update(chunk)
        if size == 0:
            raise ValueError("Empty file")
        preview = inspect_file(path, options)
        state = {"token": token, "suffix": suffix, "original_name": Path(upload.filename).name,
                 "size": size, "sha256": sha.hexdigest(), "options": options.model_dump(), "preview": preview}
        managed_path("tmp", token + ".json").write_text(json.dumps(state, default=str), encoding="utf-8")
        return state
    except Exception:
        path.unlink(missing_ok=True)
        raise


def inspect_file(path: Path, options: ImportOptions) -> dict:
    if path.suffix == ".parquet":
        con = duckdb.connect()
        try:
            columns = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
            names = [column[0] for column in columns]
            normalized = [name.casefold() for name in names]
            if len(set(normalized)) != len(names) or "__source_row_id" in normalized:
                raise ValueError("Parquet has duplicate or reserved column names")
            count = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
            rows = con.execute("SELECT * FROM read_parquet(?) LIMIT 20", [str(path)]).fetchall()
            return {"columns": [{"name": c[0], "inferred_type": c[1]} for c in columns],
                    "row_count": count, "issue_count": 0, "rows": json_value(rows)}
        finally:
            con.close()
    columns = None
    count = 0
    issue_count = 0
    issues = []
    sample: list[list[str | None]] = []
    for event in csv_rows(path, options):
        if event[0] == "header":
            columns = event[1]
        elif event[0] == "row":
            count += 1
            if len(sample) < 100:
                sample.append(event[2])
        else:
            issue_count += 1
            if len(issues) < 20:
                issues.append({"line": event[1], "reason": event[4]})
    if columns is None:
        raise ValueError("CSV contains no header or records")
    schema = [{"name": col, "inferred_type": infer_type([r[i] for r in sample])}
              for i, col in enumerate(columns)]
    return {"columns": schema, "row_count": count, "issue_count": issue_count,
            "issues": issues[:20], "rows": sample[:20]}


def commit_import(project_id: str, token: str, name: str, alias: str | None,
                  type_overrides: dict[str, str] | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid import token")
    state_path = managed_path("tmp", token + ".json")
    if not state_path.is_file():
        raise ValueError("Staged import is missing, expired, or already committed")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    tmp = managed_path("tmp", token + state["suffix"])
    if not tmp.is_file():
        raise ValueError("Staged file is missing")
    dataset_id = new_id()
    chosen_alias = safe_alias(alias or name)
    if chosen_alias in {"select", "from", "where", "join", "group", "order"}:
        chosen_alias = "dataset_" + chosen_alias
    raw_path = managed_path("raw", dataset_id + state["suffix"])
    data_path = managed_path("data", dataset_id + ".parquet")
    overrides = type_overrides or {}
    schema = state["preview"]["columns"]
    permitted = {"VARCHAR", "BIGINT", "DOUBLE", "DECIMAL", "DATE", "TIMESTAMP", "BOOLEAN"}
    if set(overrides) - {c["name"] for c in schema} or any(v not in permitted for v in overrides.values()):
        raise ValueError("Invalid type override")
    for col in schema:
        col["declared_type"] = overrides.get(col["name"], col["inferred_type"])
    with connect() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
        if db.execute("SELECT 1 FROM datasets WHERE project_id=? AND alias=?", (project_id, chosen_alias)).fetchone():
            raise ValueError(f"SQL alias already exists in this project: {chosen_alias}")
    issues = []
    count = 0
    accepted_path = managed_path("tmp", dataset_id + ".accepted.csv")
    try:
        if state["suffix"] == ".csv":
            # Normalize accepted records once, preserving row IDs and raw values.
            # QUOTE_NOTNULL (Python 3.12+) distinguishes NULL from quoted empty text.
            # Bulk COPY avoids a DuckDB transaction/insert for every individual row.
            with accepted_path.open("w", newline="", encoding="utf-8") as accepted:
                writer = csv.writer(accepted, quoting=csv.QUOTE_NOTNULL)
                writer.writerow(["__source_row_id", *(c["name"] for c in schema)])
                for event in csv_rows(tmp, ImportOptions.model_validate(state["options"])):
                    if event[0] == "row":
                        row_id, values = event[1], event[2]
                        for i, col in enumerate(schema):
                            if not type_valid(values[i], col["declared_type"]):
                                issues.append((event[1], col["name"], str(values[i]),
                                               f"Cannot parse as {col['declared_type']}; value preserved"))
                        writer.writerow([row_id, *values])
                        count += 1
                    elif event[0] == "reject":
                        issues.append((event[1], event[2], event[3], event[4]))
                    if len(issues) > 100000:
                        raise ValueError("Import has more than 100,000 issues; fix the source or split the file")
            con = duckdb.connect(config={"memory_limit": MEMORY_LIMIT, "threads": "2"})
            try:
                columns = "{" + ", ".join([
                    f"{sql_literal('__source_row_id')}: 'BIGINT'",
                    *(f"{sql_literal(c['name'])}: 'VARCHAR'" for c in schema)
                ]) + "}"
                con.execute(
                    f"COPY (SELECT * FROM read_csv({sql_literal(accepted_path)}, "
                    f"auto_detect=false, header=true, delim=',', quote='\"', escape='\"', "
                    f"columns={columns}, nullstr='', allow_quoted_nulls=false, parallel=false)) "
                    f"TO {sql_literal(data_path)} (FORMAT parquet)")
            finally:
                con.close()
        else:
            con = duckdb.connect()
            try:
                select_cols = ", ".join(quote_ident(c["name"]) for c in schema)
                con.execute(f"COPY (SELECT row_number() OVER () AS __source_row_id, {select_cols} FROM read_parquet({sql_literal(tmp)})) TO {sql_literal(data_path)} (FORMAT parquet)")
                count = state["preview"]["row_count"]
            finally:
                con.close()
        tmp.replace(raw_path)
        with connect() as db:
            one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (dataset_id, project_id, name, chosen_alias, state["original_name"], str(raw_path),
                        str(data_path), state["sha256"], state["size"], count, len(issues),
                        json.dumps(schema), json.dumps(state["options"]), now()))
            db.executemany("INSERT INTO import_rejects VALUES (?,?,?,?,?,?)",
                           [(new_id(), dataset_id, line, col, raw, reason) for line, col, raw, reason in issues])
        state_path.unlink(missing_ok=True)
        return {"id": dataset_id, "project_id": project_id, "name": name, "alias": chosen_alias,
                "row_count": count, "issue_count": len(issues), "schema": schema}
    except Exception:
        data_path.unlink(missing_ok=True)
        if raw_path.exists():
            raw_path.replace(tmp)
        raise
    finally:
        accepted_path.unlink(missing_ok=True)
