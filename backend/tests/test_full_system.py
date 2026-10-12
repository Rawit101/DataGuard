"""Edge cases found while auditing the complete user workflow."""

import duckdb
import pytest
from pathlib import Path

from backend.app import config, db, engine
from backend.tests.test_real_functionality import definition, setup_data


def project(client, name="Audit"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()


def inspect(client, filename, raw, options="{}"):
    return client.post("/api/imports/inspect", files={"file": (filename, raw)},
                       data={"options": options})


def commit(client, project_id, stage, alias, overrides=None):
    return client.post("/api/imports/commit", json={"project_id": project_id,
        "token": stage["token"], "name": alias, "alias": alias,
        "type_overrides": overrides or {}})


def test_invalid_import_options_and_reused_stage_are_clear_errors(client):
    assert inspect(client, "empty.csv", b"").status_code == 400
    assert inspect(client, "notes.txt", b"a\n1\n").status_code == 400
    assert inspect(client, "dupes.csv", b"a,a\n1,2\n").status_code == 400
    assert inspect(client, "dupes_case.csv", b"A,a\n1,2\n").status_code == 400
    assert inspect(client, "reserved_case.csv", b"__SOURCE_ROW_ID\n1\n").status_code == 400
    assert inspect(client, "multi.csv", b"a||b\n1||2\n",
                   '{"delimiter":"||"}').status_code in {400, 422}
    p = project(client)
    staged = inspect(client, "valid.csv", b"a\n1\n")
    assert staged.status_code == 200, staged.text
    assert commit(client, p["id"], staged.json(), "valid").status_code == 200
    reused = commit(client, p["id"], staged.json(), "valid_again")
    assert reused.status_code == 400
    assert len(client.get(f"/api/projects/{p['id']}/datasets").json()) == 1


def test_boolean_override_preserves_bad_value_and_reports_issue(client):
    p = project(client)
    staged = inspect(client, "flags.csv", b"flag\ntrue\nbogus\nfalse\n")
    assert staged.status_code == 200, staged.text
    result = commit(client, p["id"], staged.json(), "flags", {"flag": "BOOLEAN"})
    assert result.status_code == 200, result.text
    assert result.json()["issue_count"] == 1
    issues = client.get(f"/api/datasets/{result.json()['id']}/issues").json()
    assert issues["rows"][0]["raw_line"] == "bogus"
    rows = client.get(f"/api/datasets/{result.json()['id']}/rows").json()["rows"]
    assert [r["flag"] for r in rows] == ["true", "bogus", "false"]


def test_reserved_parquet_column_rejected_at_inspection(client):
    for column in ("__source_row_id", "__SOURCE_ROW_ID"):
        path = config.WORKSPACE / (column + ".parquet")
        with duckdb.connect() as con:
            con.execute(f"COPY (SELECT 1 AS {column}) TO '{path}' (FORMAT parquet)")
        result = inspect(client, path.name, path.read_bytes())
        assert result.status_code == 400
        assert "reserved" in result.text.lower()


def test_idempotency_key_cannot_cross_project_or_pipeline(client):
    first = project(client, "First")
    second = project(client, "Second")
    first_stage = inspect(client, "one.csv", b"n\n1\n")
    second_stage = inspect(client, "two.csv", b"n\n2\n")
    first_ds = commit(client, first["id"], first_stage.json(), "items")
    second_ds = commit(client, second["id"], second_stage.json(), "items")
    assert first_ds.status_code == second_ds.status_code == 200
    headers = {"Idempotency-Key": "shared-export-key"}
    body = {"project_id": first["id"], "sql": "SELECT * FROM items",
            "name": "items.csv", "format": "csv"}
    first_job = client.post("/api/queries/export", json=body, headers=headers)
    assert first_job.status_code == 200, first_job.text
    assert client.post("/api/queries/export", json=body, headers=headers).json()["id"] == first_job.json()["id"]
    second_job = client.post("/api/queries/export", json={**body, "project_id": second["id"]}, headers=headers)
    assert second_job.status_code == 400
    def pipeline(dataset_id):
        return {"source_dataset_id": dataset_id, "steps": [], "pre_rule_ids": [],
                "post_rule_ids": [], "strict_quality": False,
                "output": {"format": "csv", "filename": "output.csv"}}
    ids = []
    for p, ds in [(first, first_ds.json()), (second, second_ds.json())]:
        response = client.post("/api/pipelines", json={"project_id": p["id"],
            "name": "Copy", "definition": pipeline(ds["id"])})
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    run_headers = {"Idempotency-Key": "shared-run-key"}
    first_run = client.post(f"/api/pipelines/{ids[0]}/run", headers=run_headers)
    assert first_run.status_code == 200, first_run.text
    assert client.post(f"/api/pipelines/{ids[0]}/run", headers=run_headers).json()["id"] == first_run.json()["id"]
    assert client.post(f"/api/pipelines/{ids[1]}/run", headers=run_headers).status_code == 400


def test_csv_encoding_header_and_null_options(client):
    p = project(client)
    for filename, raw, options, expected in [
        ("semicolon.csv", "name;amount\nJosé;NA\nAna;2\n".encode("latin-1"),
         '{"delimiter":";","encoding":"latin-1","null_tokens":["NA"]}',
         ("José", None)),
        ("bom.csv", "city,score\nกรุงเทพ,7\n".encode("utf-8-sig"), "{}", ("กรุงเทพ", "7")),
        ("utf16.csv", "city,score\nเชียงใหม่,8\n".encode("utf-16"),
         '{"encoding":"utf-16"}', ("เชียงใหม่", "8")),
        ("no_header.csv", b"alpha|1\nbeta|2\n",
         '{"delimiter":"|","header":false}', ("alpha", "1")),
    ]:
        staged = inspect(client, filename, raw, options)
        assert staged.status_code == 200, staged.text
        assert staged.json()["preview"]["row_count"] == (2 if filename in {"semicolon.csv", "no_header.csv"} else 1)
        committed = commit(client, p["id"], staged.json(), filename[:-4])
        assert committed.status_code == 200, committed.text
        row = client.get(f"/api/datasets/{committed.json()['id']}/rows").json()["rows"][0]
        assert tuple(value for key, value in row.items() if key != "__source_row_id") == expected


def test_custom_null_token_keeps_empty_text_and_raw_bytes(client):
    p = project(client)
    raw = b"label,value\nblank,\nmissing,NA\n"
    staged = inspect(client, "nulls.csv", raw, '{"null_tokens":["NA"]}')
    assert staged.status_code == 200, staged.text
    ds = commit(client, p["id"], staged.json(), "nulls")
    assert ds.status_code == 200, ds.text
    rows = client.get(f"/api/datasets/{ds.json()['id']}/rows").json()["rows"]
    assert [r["value"] for r in rows] == ["", None]
    with db.connect() as connection:
        raw_path = connection.execute("SELECT raw_path FROM datasets WHERE id=?",
                                      (ds.json()["id"],)).fetchone()[0]
    assert Path(raw_path).read_bytes() == raw


def test_all_quality_rule_kinds_and_bad_config(client):
    p, _, source, reference = setup_data(client)
    cases = [
        ("required", {"column": "amount"}, 1),
        ("unique", {"columns": ["order_id"]}, 2),
        ("parse_valid", {"column": "amount", "type": "DOUBLE"}, 1),
        ("range", {"column": "amount", "type": "DOUBLE", "min": 100, "max": 300}, 3),
        ("accepted", {"column": "zone_code", "values": ["N", "S"]}, 1),
        ("regex", {"column": "order_id", "pattern": "H[1-5]"}, 3),
        ("foreign_key", {"column": "zone_code", "reference_dataset_id": reference["id"],
                         "reference_column": "zone_code"}, 1),
        ("row_count", {"min": 10}, 1),
    ]
    for kind, cfg, expected in cases:
        payload = {"project_id": p["id"], "dataset_id": source["id"], "name": kind,
                   "kind": kind, "severity": "warning", "config": cfg}
        created = client.post("/api/quality-rules", json=payload)
        assert created.status_code == 200, (kind, created.text)
        result = client.post(f"/api/quality-rules/{created.json()['id']}/run")
        assert result.status_code == 200, (kind, result.text)
        assert result.json()["evaluated_rows"] == 9
        assert result.json()["failed_rows"] == expected, (kind, result.json())
        assert result.json()["status"] == "failed"
    invalid = {"project_id": p["id"], "dataset_id": source["id"], "name": "Invalid accepted",
               "kind": "accepted", "severity": "warning",
               "config": {"column": "zone_code", "values": ["N", None]}}
    assert client.post("/api/quality-rules", json=invalid).status_code == 400
    invalid["config"] = {"column": "unknown", "values": ["N"]}
    assert client.post("/api/quality-rules", json=invalid).status_code == 400
    invalid["kind"] = "row_count"
    invalid["config"] = {"min": 20, "max": 10}
    assert client.post("/api/quality-rules", json=invalid).status_code == 400
    invalid["kind"] = "regex"
    invalid["config"] = {"column": "order_id", "pattern": "("}
    assert client.post("/api/quality-rules", json=invalid).status_code == 400


def test_pipeline_rejects_reserved_aggregate_alias_and_bad_filter_match(client):
    p, _, source, _ = setup_data(client)
    base = {"source_dataset_id": source["id"], "pre_rule_ids": [], "post_rule_ids": [],
            "strict_quality": False, "output": {"format": "csv", "filename": "out.csv"}}
    for step in [
        {"type": "group", "group_by": ["zone_code"], "aggregations":
         [{"function": "count", "as": "__source_count"}]},
        {"type": "filter", "match": "neither", "conditions":
         [{"column": "zone_code", "op": "eq", "value": "N"}]},
    ]:
        response = client.post("/api/pipelines/preview", json={"project_id": p["id"],
            "name": "Invalid step", "definition": {**base, "steps": [step]}})
        assert response.status_code == 400, response.text
    grouped = {**base, "steps": [
        {"type": "group", "group_by": ["zone_code"], "aggregations":
         [{"function": "count", "as": "orders"}]},
        {"type": "deduplicate", "keys": ["zone_code"], "order_by": "orders"}]}
    response = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": "No row identity", "definition": grouped})
    assert response.status_code == 400 and "row identity" in response.text


def test_invalid_column_returns_actionable_sql_error(client):
    p = project(client)
    stage = inspect(client, "items.csv", b"n\n1\n")
    assert commit(client, p["id"], stage.json(), "items").status_code == 200
    response = client.post("/api/queries/run", json={"project_id": p["id"],
        "sql": "SELECT missing_column FROM items"})
    assert response.status_code == 400
    assert "missing_column" in response.text


def test_sql_timeout_interrupts_expensive_approved_query(client, monkeypatch):
    p = project(client)
    path = config.WORKSPACE / "timeout.parquet"
    with duckdb.connect() as con:
        con.execute("COPY (SELECT i AS n FROM range(10000) t(i)) TO ? (FORMAT parquet)", [str(path)])
    stage = inspect(client, path.name, path.read_bytes())
    assert stage.status_code == 200, stage.text
    assert commit(client, p["id"], stage.json(), "big").status_code == 200
    monkeypatch.setattr(engine, "QUERY_TIMEOUT_SECONDS", 0.02)
    response = client.post("/api/queries/run", json={"project_id": p["id"],
        "sql": "SELECT sum(a.n + b.n) AS total FROM big a CROSS JOIN big b"})
    assert response.status_code == 400, response.text
    assert "interrupt" in response.text.lower()


def test_runnable_sql_script_contains_real_pipeline_plan(client):
    p, _, source, reference = setup_data(client)
    plan = definition(source["id"], reference["id"])
    response = client.post("/api/pipelines/script", json={"project_id": p["id"],
        "name": "Harbor", "definition": plan})
    assert response.status_code == 200, response.text
    assert "CREATE OR REPLACE VIEW \"harbor_orders\"" in response.text
    assert "CREATE OR REPLACE VIEW \"zone_lookup\"" in response.text
    assert "Deduplicate" not in response.text
    assert 'row_number() OVER' in response.text
    assert '"zone_zone_name"' in response.text


def test_empty_sql_result_and_prohibited_external_operations(client):
    p, _, _, _ = setup_data(client)
    empty = client.post("/api/queries/run", json={"project_id": p["id"],
        "sql": "SELECT * FROM harbor_orders WHERE order_id='absent'"})
    assert empty.status_code == 200 and empty.json()["rows"] == []
    assert empty.json()["total_rows"] == 0
    for sql in ["ATTACH '/tmp/unapproved.db' AS x", "INSTALL httpfs", "LOAD httpfs",
                "PRAGMA enable_external_access=true", "SELECT * FROM read_parquet('/tmp/secret.parquet')",
                "SELECT * FROM read_csv('https://example.com/private.csv')"]:
        result = client.post("/api/queries/run", json={"project_id": p["id"], "sql": sql})
        assert result.status_code == 400, (sql, result.text)


def test_composite_join_and_division_by_zero(client):
    p = project(client)
    left = inspect(client, "left.csv", b"k1,k2,denom,ref_k1\nA,1,2,shadow\nA,2,0,shadow\nB,1,4,shadow\nX,1,5,shadow\n")
    right = inspect(client, "right.csv", b"k1,k2,label\nA,1,first\nA,2,second\nB,1,third\n")
    left_ds = commit(client, p["id"], left.json(), "left_items")
    right_ds = commit(client, p["id"], right.json(), "right_items")
    assert left_ds.status_code == right_ds.status_code == 200
    base = {"source_dataset_id": left_ds.json()["id"], "pre_rule_ids": [],
        "post_rule_ids": [], "strict_quality": False,
        "output": {"format": "parquet", "filename": "joined.parquet"}}
    join = {"type": "join", "reference_dataset_id": right_ds.json()["id"],
            "join_type": "left", "keys": [{"left": "k1", "right": "k1"},
                                          {"left": "k2", "right": "k2"}], "prefix": "right_"}
    cast = {"type": "cast", "column": "denom", "target_type": "DOUBLE", "failure_policy": "error"}
    calculate = {"type": "calculate", "as": "ratio", "expression": {"op": "divide",
                 "left": {"value": 10}, "right": {"column": "denom"}}}
    response = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": "Composite", "definition": {**base, "steps": [cast, join, calculate]}})
    assert response.status_code == 200, response.text
    rows = {r["k1"]+r["k2"]: r for r in response.json()["rows"]}
    assert {key: (r["right_label"], r["ratio"]) for key, r in rows.items()} == {
        "A1": ("first", 5.0), "A2": ("second", None), "B1": ("third", 2.5), "X1": (None, 2.0)}
    assert response.json()["join_diagnostics"][0]["unmatched_left_rows"] == 1
    collision = {**join, "prefix": "ref_"}
    result = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": "Collision", "definition": {**base, "steps": [collision]}})
    assert result.status_code == 400


def test_export_job_is_marked_interrupted_on_restart(client):
    p = project(client)
    stage = inspect(client, "items.csv", b"n\n1\n")
    assert commit(client, p["id"], stage.json(), "items").status_code == 200
    job = client.post("/api/queries/export", json={"project_id": p["id"],
        "sql": "SELECT * FROM items", "name": "interrupted.csv", "format": "csv"}).json()
    with db.connect() as connection:
        connection.execute("UPDATE export_jobs SET status='running' WHERE id=?", (job["id"],))
    db.initialize_db()
    detail = client.get(f"/api/export-jobs/{job['id']}").json()
    assert detail["status"] == "interrupted" and detail["artifact_id"] is None


def test_join_incompatible_types_reports_an_error(client):
    p = project(client)
    path = config.WORKSPACE / "numeric_key.parquet"
    with duckdb.connect() as con:
        con.execute("COPY (SELECT 1 AS key) TO ? (FORMAT parquet)", [str(path)])
    numeric = inspect(client, path.name, path.read_bytes())
    text_key = inspect(client, "text_key.csv", b"key,label\nnot-a-number,other\n")
    source = commit(client, p["id"], numeric.json(), "numeric_key")
    reference = commit(client, p["id"], text_key.json(), "text_key")
    assert source.status_code == reference.status_code == 200
    plan = {"source_dataset_id": source.json()["id"], "pre_rule_ids": [],
        "post_rule_ids": [], "strict_quality": False,
        "output": {"format": "csv", "filename": "out.csv"},
        "steps": [{"type": "join", "reference_dataset_id": reference.json()["id"],
                   "join_type": "left", "keys": [{"left": "key", "right": "key"}],
                   "prefix": "ref_"}]}
    result = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": "Incompatible", "definition": plan})
    assert result.status_code == 400
    assert "Conversion Error" in result.text


@pytest.mark.parametrize("kind", ["select", "drop", "rename", "trim", "case", "fill",
                                  "drop_missing", "cast", "deduplicate", "filter",
                                  "calculate", "join", "group", "sort"])
def test_each_transformation_alone_on_known_fixture(client, kind):
    p, _, source, reference = setup_data(client)
    steps = {
        "select": {"type": "select", "columns": ["order_id", "amount"]},
        "drop": {"type": "drop", "columns": ["customer"]},
        "rename": {"type": "rename", "mapping": {"zone_code": "zone"}},
        "trim": {"type": "trim", "columns": ["customer"]},
        "case": {"type": "case", "columns": ["zone_code"], "mode": "lower"},
        "fill": {"type": "fill", "values": {"customer": "Unknown"}},
        "drop_missing": {"type": "drop_missing", "columns": ["amount"]},
        "cast": {"type": "cast", "column": "amount", "target_type": "DOUBLE", "failure_policy": "null"},
        "deduplicate": {"type": "deduplicate", "keys": ["order_id"],
                        "order_by": "__source_row_id", "keep": "first"},
        "filter": {"type": "filter", "conditions": [{"column": "zone_code", "op": "eq", "value": "X"}]},
        "calculate": {"type": "calculate", "as": "tag", "expression": {"op": "concat",
                      "left": {"column": "order_id"}, "right": {"value": "!"}}},
        "join": {"type": "join", "reference_dataset_id": reference["id"],
                 "join_type": "left", "keys": [{"left": "zone_code", "right": "zone_code"}],
                 "prefix": "zone_"},
        "group": {"type": "group", "group_by": ["zone_code"],
                  "aggregations": [{"function": "count", "as": "orders"}]},
        "sort": {"type": "sort", "by": [{"column": "order_id", "direction": "desc"}]},
    }
    plan = {"source_dataset_id": source["id"], "steps": [steps[kind]],
            "pre_rule_ids": [], "post_rule_ids": [], "strict_quality": False,
            "output": {"format": "parquet", "filename": "single.parquet"}}
    response = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": kind, "definition": plan})
    assert response.status_code == 200, (kind, response.text)
    rows = response.json()["rows"]
    expected_count = {"drop_missing": 8, "deduplicate": 8, "filter": 1, "group": 3}.get(kind, 9)
    assert response.json()["total_rows"] == expected_count
    by_id = {row.get("order_id"): row for row in rows}
    if kind == "select":
        assert set(rows[0]) == {"__source_row_id", "order_id", "amount"}
    elif kind == "drop":
        assert "customer" not in rows[0]
    elif kind == "rename":
        assert "zone" in rows[0] and "zone_code" not in rows[0]
    elif kind == "trim":
        assert by_id["H1"]["customer"] == "กานต์"
    elif kind == "case":
        assert by_id["H1"]["zone_code"] == "n"
    elif kind == "fill":
        assert by_id["H6"]["customer"] == "Unknown"
    elif kind == "cast":
        assert by_id["H3"]["amount"] is None and by_id["H1"]["amount"] == 100.0
    elif kind == "deduplicate":
        assert by_id["H2"]["__source_row_id"] == 2
    elif kind == "filter":
        assert rows[0]["order_id"] == "H5"
    elif kind == "calculate":
        assert by_id["H1"]["tag"] == "H1!"
    elif kind == "join":
        assert by_id["H1"]["zone_zone_name"] == "เหนือ"
        assert by_id["H5"]["zone_zone_name"] is None
    elif kind == "group":
        assert {r["zone_code"]: r["orders"] for r in rows} == {"N": 5, "S": 3, "X": 1}
    elif kind == "sort":
        assert rows[0]["order_id"] == "H8"
