from __future__ import annotations

import json
import re

from .config import managed_path
from .db import connect, new_id, now, one
from .engine import data_connection, rows_as_dicts
from .imports import quote_ident, sql_literal


KINDS = {"required", "unique", "parse_valid", "range", "accepted", "regex", "foreign_key", "row_count"}
SEVERITIES = {"warning", "error"}
TYPES = {"BIGINT", "DOUBLE", "DECIMAL", "DATE", "TIMESTAMP", "BOOLEAN"}


def rule_condition(rule: dict, columns: set[str], project_id: str) -> str | None:
    kind = rule["kind"]
    cfg = json.loads(rule["config_json"]) if isinstance(rule["config_json"], str) else rule["config_json"]
    if kind not in KINDS:
        raise ValueError("Unknown quality rule")
    if kind == "row_count":
        return None
    names = cfg.get("columns") or ([cfg["column"]] if cfg.get("column") else [])
    if not names or any(n not in columns for n in names):
        raise ValueError("Rule refers to an unknown column")
    fields = [quote_ident(n) for n in names]
    col = fields[0]
    if kind == "required":
        return " OR ".join(f"({f} IS NULL{' OR trim(cast(' + f + " AS VARCHAR)) = ''" if cfg.get("blank_is_missing") else ''})" for f in fields)
    if kind == "unique":
        nonnull = " AND ".join(f"{f} IS NOT NULL" for f in fields)
        partition = ", ".join(fields)
        return f"({nonnull}) AND count(*) OVER (PARTITION BY {partition}) > 1"
    if kind == "parse_valid":
        typ = cfg.get("type", "DOUBLE").upper()
        if typ not in TYPES:
            raise ValueError("Unsupported parse type")
        return f"{col} IS NOT NULL AND try_cast({col} AS {typ}) IS NULL"
    if kind == "range":
        typ = cfg.get("type", "DOUBLE").upper()
        if typ not in TYPES:
            raise ValueError("Unsupported range type")
        checks = []
        if cfg.get("min") is not None:
            checks.append(f"try_cast({col} AS {typ}) < {sql_literal(cfg['min'])}")
        if cfg.get("max") is not None:
            checks.append(f"try_cast({col} AS {typ}) > {sql_literal(cfg['max'])}")
        if not checks:
            raise ValueError("Range needs a minimum or maximum")
        return f"{col} IS NOT NULL AND (try_cast({col} AS {typ}) IS NULL OR {' OR '.join(checks)})"
    if kind == "accepted":
        values = cfg.get("values")
        if not isinstance(values, list) or not values or any(value is None for value in values):
            raise ValueError("Accepted values must be a nonempty list without null")
        return f"{col} IS NOT NULL AND {col} NOT IN ({', '.join(sql_literal(x) for x in values)})"
    if kind == "regex":
        pattern = cfg.get("pattern", "")
        if not pattern or len(pattern) > 500:
            raise ValueError("Invalid pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid pattern: {exc}") from exc
        return f"{col} IS NOT NULL AND NOT regexp_full_match({col}, {sql_literal(pattern)})"
    if kind == "foreign_key":
        with connect() as db:
            ref = one(db, "SELECT * FROM datasets WHERE id=? AND project_id=?",
                      (cfg.get("reference_dataset_id"), project_id))
        ref_schema = {c["name"] for c in json.loads(ref["schema_json"])}
        ref_col = cfg.get("reference_column")
        if ref_col not in ref_schema:
            raise ValueError("Unknown reference column")
        return (f"{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {quote_ident(ref['alias'])} AS ref "
                f"WHERE ref.{quote_ident(ref_col)} = source.{col})")
    raise ValueError("Unsupported quality rule")


def validate_rule_for_dataset(kind: str, config: dict, dataset_id: str, project_id: str) -> None:
    with connect() as db:
        dataset = one(db, "SELECT schema_json FROM datasets WHERE id=? AND project_id=?",
                      (dataset_id, project_id))
    if kind == "row_count":
        minimum, maximum = config.get("min"), config.get("max")
        if (minimum is None and maximum is None or
            any(value is not None and (type(value) is not int or value < 0)
                for value in (minimum, maximum)) or
            minimum is not None and maximum is not None and minimum > maximum):
            raise ValueError("Row count needs nonnegative integer bounds in order")
    columns = {c["name"] for c in json.loads(dataset["schema_json"])} | {"__source_row_id"}
    rule_condition({"kind": kind, "config_json": config}, columns, project_id)


def evaluate_rule(con, rule: dict, alias: str, columns: set[str], project_id: str,
                  persist: bool = True, artifact_id: str | None = None) -> dict:
    cfg = json.loads(rule["config_json"]) if isinstance(rule["config_json"], str) else rule["config_json"]
    table = quote_ident(alias)
    evaluated = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if rule["kind"] == "row_count":
        minimum, maximum = cfg.get("min"), cfg.get("max")
        failed = int((minimum is not None and evaluated < minimum) or
                     (maximum is not None and evaluated > maximum))
        examples = []
        where = None
    else:
        where = rule_condition(rule, columns, project_id)
        if rule["kind"] == "unique":
            # Window functions cannot occur in WHERE; the inner query computes the group size.
            names = cfg.get("columns") or [cfg["column"]]
            parts = ", ".join(quote_ident(n) for n in names)
            condition = " AND ".join(f"{quote_ident(n)} IS NOT NULL" for n in names)
            failed_sql = (f"SELECT * EXCLUDE (__duplicate_count) FROM "
                          f"(SELECT *, count(*) OVER (PARTITION BY {parts}) AS __duplicate_count FROM {table}) AS source "
                          f"WHERE ({condition}) AND __duplicate_count > 1")
        else:
            failed_sql = f"SELECT * FROM {table} AS source WHERE {where}"
        failed = con.execute(f"SELECT count(*) FROM ({failed_sql})").fetchone()[0]
        cursor = con.execute(f"SELECT * FROM ({failed_sql}) LIMIT 20")
        examples = rows_as_dicts(cursor, cursor.fetchall())
    result = {"rule_id": rule["id"], "name": rule["name"], "kind": rule["kind"],
              "severity": rule["severity"], "evaluated_rows": evaluated,
              "failed_rows": failed, "status": "failed" if failed else "passed", "examples": examples}
    if persist:
        run_id = new_id()
        output = managed_path("exports", artifact_id + ".csv") if artifact_id else None
        try:
            if failed and where is not None:
                if output is None:
                    raise ValueError("A managed quality artifact ID is required")
                # Persist all failing records, while the response carries only bounded examples.
                con.execute(f"COPY (SELECT *, {sql_literal(rule['name'])} AS rule_name, "
                            f"{sql_literal(rule['kind'])} AS reason FROM ({failed_sql})) "
                            f"TO {sql_literal(output)} (FORMAT CSV, HEADER TRUE)")
            else:
                artifact_id = None
            with connect() as db:
                if artifact_id:
                    db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                               (artifact_id, project_id, None, "quality_failures", "csv", str(output),
                                f"{rule['name']}_failures.csv", failed, now()))
                db.execute("INSERT INTO quality_runs VALUES (?,?,?,?,?,?,?,?)",
                           (run_id, rule["id"], result["status"], evaluated, failed,
                            json.dumps(examples, default=str), artifact_id, now()))
        except Exception:
            if output is not None:
                output.unlink(missing_ok=True)
            raise
        result["id"] = run_id
        result["artifact_id"] = artifact_id
    return result


def run_rule(rule_id: str) -> dict:
    with connect() as db:
        rule = one(db, "SELECT * FROM quality_rules WHERE id=?", (rule_id,))
        ds = one(db, "SELECT * FROM datasets WHERE id=?", (rule["dataset_id"],))
    artifact_id = new_id()
    path = managed_path("exports", artifact_id + ".csv")
    with data_connection(ds["project_id"], [path]) as con:
        return evaluate_rule(con, rule, ds["alias"],
                             {c["name"] for c in json.loads(ds["schema_json"])} | {"__source_row_id"},
                             ds["project_id"], persist=True, artifact_id=artifact_id)
