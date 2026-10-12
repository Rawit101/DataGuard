from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from .config import MEMORY_LIMIT, QUERY_TIMEOUT_SECONDS, managed_path
from .db import connect as metadata, many, new_id, now, one
from .imports import quote_ident, sql_literal


SAFE_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "ROUND", "ABS", "LOWER", "UPPER",
    "TRIM", "LTRIM", "RTRIM", "SUBSTR", "SUBSTRING", "COALESCE", "NULLIF",
    "TRY_CAST", "CAST", "DATE_TRUNC", "STRFTIME", "REGEXP_MATCHES", "CONCAT",
    "LENGTH", "REPLACE", "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD",
    "FIRST_VALUE", "LAST_VALUE", "IF", "GREATEST", "LEAST", "EXTRACT",
    "DATE_PART", "FLOOR", "CEIL", "CEILING", "POWER", "SQRT", "STDDEV",
    "COUNT_IF", "BOOL_AND", "BOOL_OR", "MEDIAN", "NTILE", "PERCENT_RANK",
}


def json_value(value):
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def rows_as_dicts(cursor, rows):
    keys = [d[0] for d in cursor.description]
    return [{k: json_value(v) for k, v in zip(keys, row)} for row in rows]


def project_datasets(project_id: str) -> list[dict]:
    with metadata() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
        return many(db, "SELECT * FROM datasets WHERE project_id=? ORDER BY created_at", (project_id,))


@contextmanager
def data_connection(project_id: str, extra_paths: list[Path] | None = None):
    datasets = project_datasets(project_id)
    paths = [d["data_path"] for d in datasets] + [str(p) for p in (extra_paths or [])]
    con = duckdb.connect(config={"memory_limit": MEMORY_LIMIT, "threads": "2"})
    try:
        con.execute("SET allowed_paths=?", [paths])
        con.execute("SET autoload_known_extensions=false")
        con.execute("SET autoinstall_known_extensions=false")
        con.execute("SET allow_community_extensions=false")
        con.execute("SET python_enable_replacements=false")
        con.execute("SET enable_external_access=false")
        for d in datasets:
            con.execute(f"CREATE VIEW {quote_ident(d['alias'])} AS SELECT * FROM read_parquet({sql_literal(d['data_path'])})")
        con.execute("SET lock_configuration=true")
        yield con
    finally:
        con.close()


def validate_sql(sql: str, aliases: set[str]) -> str:
    if not sql or len(sql) > 50_000:
        raise ValueError("SQL must be between 1 and 50,000 characters")
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:
        raise ValueError(f"Invalid SQL: {exc}") from exc
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Only one query is allowed")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Only SELECT queries and CTEs are allowed")
    cte_names = {cte.alias.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
            raise ValueError("Table functions and qualified table names are not allowed")
        if table.name.lower() not in aliases | cte_names:
            raise ValueError(f"Dataset is not approved: {table.name}")
    for func in root.find_all(exp.Func):
        name = (func.sql_name() if hasattr(func, "sql_name") else func.name).upper()
        if name not in SAFE_FUNCTIONS:
            raise ValueError(f"Function is not approved: {name}")
    for node in root.walk():
        if isinstance(node, (exp.Command, exp.Insert, exp.Update, exp.Delete, exp.Create,
                             exp.Drop, exp.Alter, exp.Copy, exp.Attach, exp.Unnest)):
            raise ValueError("Only read-only dataset queries are allowed")
    return root.sql(dialect="duckdb")


def run_user_sql(project_id: str, sql: str, page: int = 1, page_size: int = 50) -> dict:
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("Invalid page or page size")
    datasets = project_datasets(project_id)
    parsed = validate_sql(sql, {d["alias"].lower() for d in datasets})
    run_id = new_id()
    started = time.monotonic()
    try:
        with data_connection(project_id) as con:
            timer = threading.Timer(QUERY_TIMEOUT_SECONDS, con.interrupt)
            timer.start()
            try:
                cursor = con.execute(f"SELECT * FROM ({parsed}) AS result LIMIT ? OFFSET ?",
                                     [page_size, (page - 1) * page_size])
                rows = rows_as_dicts(cursor, cursor.fetchall())
                count = con.execute(f"SELECT count(*) FROM ({parsed}) AS result").fetchone()[0]
            finally:
                timer.cancel()
        duration = int((time.monotonic() - started) * 1000)
        with metadata() as db:
            db.execute("INSERT INTO query_runs VALUES (?,?,?,?,?,?,?)",
                       (run_id, project_id, sql, "completed", duration, None, now()))
        return {"rows": rows, "total_rows": count, "page": page, "page_size": page_size,
                "duration_ms": duration, "run_id": run_id, "sql": parsed}
    except Exception as exc:
        with metadata() as db:
            db.execute("INSERT INTO query_runs VALUES (?,?,?,?,?,?,?)",
                       (run_id, project_id, sql, "failed", int((time.monotonic()-started)*1000), str(exc), now()))
        raise


def dataset_page(dataset_id: str, page: int, page_size: int, sort: str | None,
                 direction: str, filter_column: str | None, filter_text: str | None) -> dict:
    if page < 1 or page_size < 1 or page_size > 100 or direction not in {"asc", "desc"}:
        raise ValueError("Invalid paging or sort options")
    with metadata() as db:
        ds = one(db, "SELECT * FROM datasets WHERE id=?", (dataset_id,))
    cols = {c["name"] for c in json.loads(ds["schema_json"])} | {"__source_row_id"}
    if sort and sort not in cols or filter_column and filter_column not in cols:
        raise ValueError("Unknown column")
    where = f"WHERE CAST({quote_ident(filter_column)} AS VARCHAR) ILIKE ?" if filter_column and filter_text else ""
    params = [f"%{filter_text}%"] if where else []
    ordering = f"ORDER BY {quote_ident(sort)} {direction.upper()}, __source_row_id" if sort else "ORDER BY __source_row_id"
    with data_connection(ds["project_id"]) as con:
        base = quote_ident(ds["alias"])
        cursor = con.execute(f"SELECT * FROM {base} {where} {ordering} LIMIT ? OFFSET ?",
                             [*params, page_size, (page-1)*page_size])
        rows = rows_as_dicts(cursor, cursor.fetchall())
        total = con.execute(f"SELECT count(*) FROM {base} {where}", params).fetchone()[0]
    return {"rows": rows, "total_rows": total, "page": page, "page_size": page_size}


def profile_dataset(dataset_id: str) -> dict:
    with metadata() as db:
        ds = one(db, "SELECT * FROM datasets WHERE id=?", (dataset_id,))
    schema = json.loads(ds["schema_json"])
    results = []
    with data_connection(ds["project_id"]) as con:
        table = quote_ident(ds["alias"])
        for col in schema:
            field = quote_ident(col["name"])
            stats = con.execute(f"SELECT count(*) FILTER (WHERE {field} IS NULL), count(DISTINCT {field}), min({field}), max({field}) FROM {table}").fetchone()
            item = {"name": col["name"], "declared_type": col["declared_type"],
                    "null_count": stats[0], "distinct_count": stats[1],
                    "min": json_value(stats[2]), "max": json_value(stats[3]),
                    "mean": None, "invalid_count": None, "summary_basis": "text"}
            declared = col["declared_type"].upper()
            if declared in {"BIGINT", "DOUBLE", "DECIMAL", "DATE", "TIMESTAMP", "BOOLEAN"}:
                typed = con.execute(
                    f"SELECT min(TRY_CAST({field} AS {declared})), max(TRY_CAST({field} AS {declared})), "
                    f"count(*) FILTER (WHERE {field} IS NOT NULL AND TRY_CAST({field} AS {declared}) IS NULL) "
                    f"FROM {table}").fetchone()
                item.update({"min": json_value(typed[0]), "max": json_value(typed[1]),
                             "invalid_count": typed[2], "summary_basis": "valid parsed values"})
                if declared in {"BIGINT", "DOUBLE", "DECIMAL"}:
                    item["mean"] = con.execute(
                        f"SELECT avg(TRY_CAST({field} AS DOUBLE)) FROM {table}").fetchone()[0]
            results.append(item)
    return {"dataset_id": dataset_id, "row_count": ds["row_count"], "columns": results,
            "metrics": "exact"}


def export_sql(project_id: str, sql: str, name: str, format: str, bom: bool = False,
               cancel_check=None) -> dict:
    if format not in {"csv", "parquet"}:
        raise ValueError("Choose CSV or Parquet")
    if not name or Path(name).name != name or len(name) > 120:
        raise ValueError("Invalid filename")
    datasets = project_datasets(project_id)
    parsed = validate_sql(sql, {d["alias"].lower() for d in datasets})
    artifact_id = new_id()
    path = managed_path("exports", artifact_id + "." + format)
    tmp = managed_path("tmp", artifact_id + "." + format)
    stamped = None
    try:
        with data_connection(project_id, [tmp]) as con:
            done = threading.Event()
            def watch():
                while not done.wait(0.25):
                    if cancel_check and cancel_check():
                        con.interrupt()
                        return
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            timer = threading.Timer(QUERY_TIMEOUT_SECONDS * 10, con.interrupt)
            timer.start()
            try:
                if cancel_check and cancel_check():
                    raise InterruptedError("Export cancelled")
                count = con.execute(f"SELECT count(*) FROM ({parsed}) AS result").fetchone()[0]
                con.execute(f"COPY ({parsed}) TO {sql_literal(tmp)} (FORMAT {format})")
            finally:
                done.set()
                watcher.join(timeout=1)
                timer.cancel()
        if cancel_check and cancel_check():
            raise InterruptedError("Export cancelled")
        if format == "csv" and bom:
            stamped = managed_path("tmp", artifact_id + ".bom.csv")
            with stamped.open("wb") as out, tmp.open("rb") as source:
                out.write(b"\xef\xbb\xbf")
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
            tmp.unlink()
            tmp = stamped
        tmp.replace(path)
        return {"id": artifact_id, "project_id": project_id, "path": str(path),
                "name": name, "format": format, "row_count": count}
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
        if stamped is not None:
            stamped.unlink(missing_ok=True)
