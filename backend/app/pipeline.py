from __future__ import annotations

import json
from typing import Any

from .db import connect, one
from .imports import quote_ident as q, sql_literal as lit


CAST_TYPES = {"VARCHAR", "BIGINT", "DOUBLE", "DECIMAL", "DATE", "TIMESTAMP", "BOOLEAN"}
AGG_FUNCS = {"count": "count", "sum": "sum", "mean": "avg", "min": "min", "max": "max"}
FILTER_OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
              "contains": "ILIKE", "is_null": "IS NULL", "not_null": "IS NOT NULL"}


def require_cols(names: list[str], columns: list[str]) -> None:
    missing = set(names) - set(columns)
    if missing:
        raise ValueError(f"Unknown columns: {', '.join(sorted(missing))}")


def expression(node: dict[str, Any], columns: list[str]) -> str:
    if not isinstance(node, dict):
        raise ValueError("Calculation must be a structured expression")
    if "column" in node:
        require_cols([node["column"]], columns)
        return q(node["column"])
    if "value" in node:
        return lit(node["value"])
    op = node.get("op")
    if op in {"add", "subtract", "multiply", "divide", "concat"}:
        left = expression(node["left"], columns)
        right = expression(node["right"], columns)
        if op == "divide":
            return f"({left} / NULLIF({right}, 0))"
        if op == "concat":
            return f"concat({left}, {right})"
        sign = {"add": "+", "subtract": "-", "multiply": "*"}[op]
        return f"({left} {sign} {right})"
    if op in {"upper", "lower", "trim", "abs", "round"}:
        inner = expression(node["arg"], columns)
        if op == "round":
            digits = int(node.get("digits", 2))
            if digits < 0 or digits > 12:
                raise ValueError("Rounding digits out of range")
            return f"round({inner}, {digits})"
        return f"{op}({inner})"
    if op == "coalesce":
        args = node.get("args", [])
        if len(args) < 2:
            raise ValueError("Coalesce needs at least two values")
        return "coalesce(" + ", ".join(expression(arg, columns) for arg in args) + ")"
    raise ValueError("Unsupported calculation operation")


def filter_condition(condition: dict, columns: list[str]) -> str:
    col = condition.get("column")
    require_cols([col], columns)
    op = condition.get("op")
    if op not in FILTER_OPS:
        raise ValueError("Unsupported filter operator")
    if op in {"is_null", "not_null"}:
        return f"{q(col)} {FILTER_OPS[op]}"
    value = condition.get("value")
    if op == "contains":
        return f"cast({q(col)} AS VARCHAR) ILIKE {lit('%' + str(value) + '%')}"
    return f"{q(col)} {FILTER_OPS[op]} {lit(value)}"


def compile_pipeline(project_id: str, definition: dict, stop_after: int | None = None) -> dict:
    source_id = definition.get("source_dataset_id")
    with connect() as db:
        source = one(db, "SELECT * FROM datasets WHERE id=? AND project_id=?", (source_id, project_id))
    columns = ["__source_row_id"] + [c["name"] for c in json.loads(source["schema_json"])]
    ctes = [f"step_0 AS (SELECT * FROM {q(source['alias'])})"]
    steps = definition.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 100:
        raise ValueError("Pipeline must have at most 100 steps")
    descriptions = []
    refs = {source_id}
    joins = []
    previous = "step_0"
    for index, step in enumerate(steps):
        if stop_after is not None and index > stop_after:
            break
        if not isinstance(step, dict):
            raise ValueError("Invalid pipeline step")
        if step.get("enabled", True) is False:
            descriptions.append({"index": index, "description": "Disabled", "enabled": False})
            continue
        kind = step.get("type")
        name = f"step_{index+1}"
        from_sql = q(previous)
        select = ", ".join(q(c) for c in columns)
        sql = None
        description = kind
        if kind == "select":
            selected = step.get("columns", [])
            require_cols(selected, columns)
            columns = ["__source_row_id"] + [c for c in selected if c != "__source_row_id"] if "__source_row_id" in columns else selected
            sql = f"SELECT {', '.join(q(c) for c in columns)} FROM {from_sql}"
            description = "Select columns: " + ", ".join(selected)
        elif kind == "drop":
            dropped = step.get("columns", [])
            require_cols(dropped, columns)
            if "__source_row_id" in dropped:
                raise ValueError("Source row identity cannot be dropped")
            columns = [c for c in columns if c not in dropped]
            sql = f"SELECT {', '.join(q(c) for c in columns)} FROM {from_sql}"
            description = "Drop columns: " + ", ".join(dropped)
        elif kind == "rename":
            mapping = step.get("mapping", {})
            require_cols(list(mapping), columns)
            if "__source_row_id" in mapping or len(set(mapping.values())) != len(mapping.values()):
                raise ValueError("Invalid rename mapping")
            new_cols = [mapping.get(c, c) for c in columns]
            if len(set(new_cols)) != len(new_cols):
                raise ValueError("Rename creates duplicate columns")
            sql = f"SELECT {', '.join(q(c) + ' AS ' + q(mapping.get(c, c)) for c in columns)} FROM {from_sql}"
            columns = new_cols
            description = "Rename columns"
        elif kind in {"trim", "case"}:
            targets = step.get("columns", [])
            require_cols(targets, columns)
            mode = step.get("mode", "lower")
            if kind == "case" and mode not in {"lower", "upper"}:
                raise ValueError("Case mode must be lower or upper")
            func = "trim" if kind == "trim" else mode
            sql = f"SELECT {', '.join((func + '(' + q(c) + ') AS ' + q(c)) if c in targets else q(c) for c in columns)} FROM {from_sql}"
            description = f"{func.title()} text in " + ", ".join(targets)
        elif kind == "fill":
            values = step.get("values", {})
            require_cols(list(values), columns)
            sql = f"SELECT {', '.join(('coalesce(' + q(c) + ', ' + lit(values[c]) + ') AS ' + q(c)) if c in values else q(c) for c in columns)} FROM {from_sql}"
            description = "Fill missing values"
        elif kind == "drop_missing":
            targets = step.get("columns", [])
            require_cols(targets, columns)
            if not targets:
                raise ValueError("Choose columns for drop missing")
            condition = " AND ".join(f"{q(c)} IS NOT NULL AND trim(cast({q(c)} AS VARCHAR)) <> ''" for c in targets)
            sql = f"SELECT {select} FROM {from_sql} WHERE {condition}"
            description = "Drop rows missing " + ", ".join(targets)
        elif kind == "cast":
            col = step.get("column")
            require_cols([col], columns)
            typ = str(step.get("target_type", "")).upper()
            policy = step.get("failure_policy", "error")
            if typ not in CAST_TYPES or policy not in {"error", "null", "drop"}:
                raise ValueError("Invalid cast type or failure policy")
            cast = f"{'cast' if policy == 'error' else 'try_cast'}({q(col)} AS {typ})"
            select = ", ".join(f"{cast} AS {q(c)}" if c == col else q(c) for c in columns)
            where = f" WHERE {q(col)} IS NULL OR try_cast({q(col)} AS {typ}) IS NOT NULL" if policy == "drop" else ""
            sql = f"SELECT {select} FROM {from_sql}{where}"
            description = f"Cast {col} to {typ} ({policy} on failure)"
        elif kind == "deduplicate":
            if "__source_row_id" not in columns:
                raise ValueError("Deduplicate requires source row identity before aggregation")
            keys = step.get("keys", [])
            require_cols(keys, columns)
            if not keys:
                raise ValueError("Choose duplicate keys")
            order = step.get("order_by", "__source_row_id")
            require_cols([order], columns)
            keep = step.get("keep", "first")
            if keep not in {"first", "last"}:
                raise ValueError("Keep policy must be first or last")
            direction = "ASC" if keep == "first" else "DESC"
            sql = (f"SELECT {select} FROM (SELECT *, row_number() OVER "
                   f"(PARTITION BY {', '.join(q(c) for c in keys)} ORDER BY {q(order)} {direction}, "
                   f"{q('__source_row_id')} {direction}) AS __rank FROM {from_sql}) WHERE __rank = 1")
            description = f"Deduplicate by {', '.join(keys)}; keep {keep} by {order}"
        elif kind == "filter":
            conditions = step.get("conditions", [])
            if not conditions:
                raise ValueError("Add a filter condition")
            if step.get("match", "all") not in {"all", "any"}:
                raise ValueError("Filter match must be all or any")
            joiner = " AND " if step.get("match", "all") == "all" else " OR "
            where = joiner.join("(" + filter_condition(c, columns) + ")" for c in conditions)
            sql = f"SELECT {select} FROM {from_sql} WHERE {where}"
            description = f"Filter {len(conditions)} condition(s)"
        elif kind == "calculate":
            output = step.get("as")
            if not output or output in columns or output.startswith("__"):
                raise ValueError("Calculation needs a new output column")
            expr = expression(step.get("expression", {}), columns)
            sql = f"SELECT {select}, {expr} AS {q(output)} FROM {from_sql}"
            columns.append(output)
            description = f"Calculate {output}"
        elif kind == "join":
            ref_id = step.get("reference_dataset_id")
            with connect() as db:
                ref = one(db, "SELECT * FROM datasets WHERE id=? AND project_id=?", (ref_id, project_id))
            ref_cols = [c["name"] for c in json.loads(ref["schema_json"])]
            keys = step.get("keys", [])
            if not keys or step.get("join_type", "left") not in {"left", "inner"}:
                raise ValueError("Join needs keys and left or inner type")
            require_cols([k["left"] for k in keys], columns)
            require_cols([k["right"] for k in keys], ref_cols)
            prefix = step.get("prefix", "ref_")
            if not prefix or not prefix.replace("_", "").isalnum():
                raise ValueError("Invalid join prefix")
            right_outputs = {c: prefix + c for c in ref_cols}
            right_outputs["__source_row_id"] = prefix + "source_row_id"
            if set(right_outputs.values()) & set(columns):
                raise ValueError("Join column names collide; change prefix")
            condition = " AND ".join(f"l.{q(k['left'])} = r.{q(k['right'])}" for k in keys)
            left_select = ", ".join(f"l.{q(c)}" for c in columns)
            right_select = ", ".join(f"r.{q(c)} AS {q(out)}" for c, out in right_outputs.items())
            sql = (f"SELECT {left_select}, {right_select} FROM {from_sql} AS l "
                   f"{step.get('join_type', 'left').upper()} JOIN {q(ref['alias'])} AS r ON {condition}")
            columns.extend(right_outputs.values())
            refs.add(ref_id)
            joins.append({"step": index, "reference_dataset_id": ref_id, "keys": keys,
                          "cardinality": step.get("cardinality", "many_to_one")})
            description = f"{step.get('join_type', 'left').title()} join {ref['alias']}"
        elif kind == "group":
            groups = step.get("group_by", [])
            aggs = step.get("aggregations", [])
            require_cols(groups, columns)
            if not aggs:
                raise ValueError("Group step needs aggregations")
            output_cols = list(groups)
            aggregate_sql = []
            for agg in aggs:
                fn, col, alias = agg.get("function"), agg.get("column"), agg.get("as")
                if fn not in AGG_FUNCS or not alias or alias in output_cols or alias.startswith("__"):
                    raise ValueError("Invalid aggregation")
                if fn != "count":
                    require_cols([col], columns)
                target = "*" if fn == "count" and not col else q(col)
                aggregate_sql.append(f"{AGG_FUNCS[fn]}({target}) AS {q(alias)}")
                output_cols.append(alias)
            # Aggregated lineage records the number of input rows, not a false one-to-one ID.
            aggregate_sql.append("count(*) AS __source_count")
            output_cols.append("__source_count")
            projection = ", ".join([*(q(c) for c in groups), *aggregate_sql])
            group_sql = " GROUP BY " + ", ".join(q(c) for c in groups) if groups else ""
            sql = f"SELECT {projection} FROM {from_sql}{group_sql}"
            columns = output_cols
            description = "Group by " + (", ".join(groups) or "all rows")
        elif kind == "sort":
            ordering = step.get("by", [])
            if not ordering:
                raise ValueError("Choose sort columns")
            require_cols([x["column"] for x in ordering], columns)
            terms = []
            for item in ordering:
                if item.get("direction", "asc") not in {"asc", "desc"}:
                    raise ValueError("Invalid sort direction")
                terms.append(f"{q(item['column'])} {item.get('direction', 'asc').upper()}")
            sql = f"SELECT {select} FROM {from_sql} ORDER BY {', '.join(terms)}"
            description = "Sort by " + ", ".join(x["column"] for x in ordering)
        else:
            raise ValueError(f"Unsupported step type: {kind}")
        ctes.append(f"{q(name)} AS ({sql})")
        previous = name
        descriptions.append({"index": index, "description": description, "enabled": True, "columns": list(columns)})
    full_sql = "WITH " + ",\n".join(ctes) + f"\nSELECT * FROM {q(previous)}"
    return {"sql": full_sql, "columns": columns, "descriptions": descriptions,
            "source_ids": sorted(refs), "joins": joins}


def join_diagnostics(con, project_id: str, definition: dict, compiled: dict) -> list[dict]:
    results = []
    for join in compiled["joins"]:
        with connect() as db:
            ref = one(db, "SELECT * FROM datasets WHERE id=? AND project_id=?",
                      (join["reference_dataset_id"], project_id))
        prior = compile_pipeline(project_id, definition, stop_after=join["step"]-1)["sql"]
        ref_table = q(ref["alias"])
        right_cols = [q(k["right"]) for k in join["keys"]]
        duplicate_groups = con.execute(
            f"SELECT count(*) FROM (SELECT {', '.join(right_cols)}, count(*) AS n FROM {ref_table} "
            f"GROUP BY {', '.join(right_cols)} HAVING count(*) > 1)").fetchone()[0]
        condition = " AND ".join(f"l.{q(k['left'])}=r.{q(k['right'])}" for k in join["keys"])
        null_condition = " OR ".join(f"l.{q(k['left'])} IS NULL" for k in join["keys"])
        null_keys = con.execute(f"SELECT count(*) FROM ({prior}) l WHERE {null_condition}").fetchone()[0]
        unmatched = con.execute(
            f"SELECT count(*) FROM ({prior}) l WHERE NOT ({null_condition}) AND NOT EXISTS "
            f"(SELECT 1 FROM {ref_table} r WHERE {condition})").fetchone()[0]
        cursor = con.execute(
            f"SELECT {', '.join('l.'+q(k['left']) for k in join['keys'])} FROM ({prior}) l "
            f"WHERE NOT ({null_condition}) AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE {condition}) LIMIT 5")
        examples = [list(row) for row in cursor.fetchall()]
        results.append({"step": join["step"], "reference_dataset_id": ref["id"],
                        "expected_cardinality": join["cardinality"], "duplicate_reference_key_groups": duplicate_groups,
                        "unmatched_left_rows": unmatched, "null_left_keys": null_keys,
                        "unmatched_key_examples": examples,
                        "warning": "Reference keys may multiply rows" if duplicate_groups else None})
    return results
