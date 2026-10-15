"""Independent fixture checks for the complete local data path."""

import csv
import io
import threading
from pathlib import Path

import duckdb

from backend.app import config, db, worker


FIXTURES = Path(__file__).parent / "fixtures"


def import_file(client, project_id, filename, alias, overrides=None):
    raw = (FIXTURES / filename).read_bytes()
    stage_response = client.post("/api/imports/inspect", files={"file": (filename, raw)},
                                 data={"options": "{}"})
    assert stage_response.status_code == 200, stage_response.text
    stage = stage_response.json()
    committed_response = client.post("/api/imports/commit", json={"project_id": project_id,
        "token": stage["token"], "name": filename, "alias": alias,
        "type_overrides": overrides or {}})
    assert committed_response.status_code == 200, committed_response.text
    return stage, committed_response.json()


def definition(source_id, reference_id):
    return {"source_dataset_id": source_id, "strict_quality": False,
            "pre_rule_ids": [], "post_rule_ids": [],
            "output": {"format": "parquet", "filename": "harbor_summary.parquet"},
            "steps": [
                {"type": "trim", "columns": ["customer", "zone_code"]},
                {"type": "drop_missing", "columns": ["amount"]},
                {"type": "cast", "column": "amount", "target_type": "DOUBLE", "failure_policy": "drop"},
                {"type": "cast", "column": "ordered_on", "target_type": "DATE", "failure_policy": "drop"},
                {"type": "deduplicate", "keys": ["order_id"], "order_by": "__source_row_id", "keep": "first"},
                {"type": "join", "reference_dataset_id": reference_id, "join_type": "left",
                 "keys": [{"left": "zone_code", "right": "zone_code"}], "prefix": "zone_",
                 "cardinality": "many_to_one"},
                {"type": "fill", "values": {"zone_zone_name": "ไม่ทราบ"}},
                {"type": "calculate", "as": "taxed_amount", "expression": {"op": "multiply",
                 "left": {"column": "amount"}, "right": {"value": 1.1}}},
                {"type": "group", "group_by": ["zone_zone_name"], "aggregations": [
                    {"function": "count", "as": "orders"},
                    {"function": "sum", "column": "amount", "as": "total_amount"}]},
                {"type": "sort", "by": [{"column": "zone_zone_name", "direction": "asc"}]},
            ]}


def setup_data(client):
    project = client.post("/api/projects", json={"name": "Harbor verification"}).json()
    stage, source = import_file(client, project["id"], "harbor_orders.csv", "harbor_orders",
                                {"amount": "DOUBLE", "ordered_on": "DATE"})
    _, reference = import_file(client, project["id"], "zone_lookup.csv", "zone_lookup")
    return project, stage, source, reference


def test_fresh_import_explorer_sql_quality_and_project_isolation(client):
    project, stage, source, reference = setup_data(client)
    project_id = project["id"]
    assert stage["preview"]["row_count"] == 9
    assert stage["preview"]["issue_count"] == 1
    assert source["row_count"] == 9 and source["issue_count"] == 3
    raw_file = config.WORKSPACE / "raw" / (source["id"] + ".csv")
    assert raw_file.read_bytes() == (FIXTURES / "harbor_orders.csv").read_bytes()
    issues = client.get(f"/api/datasets/{source['id']}/issues").json()
    assert issues["total_rows"] == 3
    assert any("Expected 5 columns, found 6" in row["reason"] for row in issues["rows"])
    assert {row["column_name"] for row in issues["rows"]} == {None, "amount", "ordered_on"}
    rows = client.get(f"/api/datasets/{source['id']}/rows", params={"page_size": 2}).json()
    assert rows["total_rows"] == 9 and [r["order_id"] for r in rows["rows"]] == ["H1", "H2"]
    filtered = client.get(f"/api/datasets/{source['id']}/rows", params={
        "sort": "amount", "direction": "desc", "filter_column": "customer", "filter_text": "กา"}).json()
    assert filtered["total_rows"] == 1 and filtered["rows"][0]["order_id"] == "H1"
    profile = client.get(f"/api/datasets/{source['id']}/profile").json()
    amount = next(c for c in profile["columns"] if c["name"] == "amount")
    assert amount["null_count"] == 1 and amount["invalid_count"] == 1
    assert amount["min"] == 50.0 and amount["max"] == 400.0
    sql = "SELECT zone_code, count(*) AS n FROM harbor_orders GROUP BY zone_code ORDER BY zone_code"
    query = client.post("/api/queries/run", json={"project_id": project_id, "sql": sql}).json()
    assert [(r["zone_code"], r["n"]) for r in query["rows"]] == [("N", 5), ("S", 3), ("X", 1)]
    saved = client.post("/api/queries", json={"project_id": project_id, "name": "Zone counts", "sql": sql}).json()
    assert client.get(f"/api/projects/{project_id}/queries").json()[0]["id"] == saved["id"]
    invalid = client.post("/api/queries/run", json={"project_id": project_id,
                                                "sql": "SELECT * FROM missing_table"})
    assert invalid.status_code == 400 and "not approved" in invalid.text
    for kind, cfg, expected, ids in [
        ("unique", {"columns": ["order_id"]}, 2, {2, 3}),
        ("parse_valid", {"column": "amount", "type": "DOUBLE"}, 1, {4}),
        ("parse_valid", {"column": "ordered_on", "type": "DATE"}, 1, {5}),
        ("foreign_key", {"column": "zone_code", "reference_dataset_id": reference["id"],
                         "reference_column": "zone_code"}, 1, {6}),
    ]:
        created = client.post("/api/quality-rules", json={"project_id": project_id,
            "dataset_id": source["id"], "name": kind, "kind": kind, "severity": "warning",
            "config": cfg}).json()
        result = client.post(f"/api/quality-rules/{created['id']}/run").json()
        assert result["evaluated_rows"] == 9 and result["failed_rows"] == expected
        assert {r["__source_row_id"] for r in result["examples"]} == ids
        exported = client.get(f"/api/artifacts/{result['artifact_id']}/download")
        assert exported.status_code == 200
        assert {int(r["__source_row_id"]) for r in csv.DictReader(io.StringIO(exported.text))} == ids
    other = client.post("/api/projects", json={"name": "Separate harbor"}).json()
    assert client.get(f"/api/projects/{other['id']}/datasets").json() == []
    assert client.get(f"/api/projects/{other['id']}/queries").json() == []
    leak = client.post("/api/queries/run", json={"project_id": other["id"], "sql": sql})
    assert leak.status_code == 400
    move_rule = client.post("/api/quality-rules", json={"project_id": project_id,
        "dataset_id": source["id"], "name": "Original", "kind": "row_count", "severity": "warning",
        "config": {"min": 1}}).json()
    moved = client.put(f"/api/quality-rules/{move_rule['id']}", json={"project_id": project_id,
        "dataset_id": reference["id"], "name": "Reference count", "kind": "row_count",
        "severity": "warning", "config": {"min": 2}})
    assert moved.status_code == 200
    assert next(r for r in client.get(f"/api/projects/{project_id}/quality-rules").json()
                if r["id"] == move_rule["id"])["dataset_id"] == reference["id"]
    assert client.put(f"/api/quality-rules/{move_rule['id']}", json={"project_id": other["id"],
        "dataset_id": reference["id"], "name": "Leak", "kind": "row_count",
        "severity": "warning", "config": {"min": 1}}).status_code == 404
    renamed = client.put(f"/api/projects/{project_id}", json={"name": "Harbor verified"}).json()
    assert renamed["name"] == "Harbor verified"
    db.initialize_db()
    assert client.get(f"/api/projects/{project_id}/overview").json()["project"]["name"] == "Harbor verified"


def test_fresh_pipeline_version_exports_and_changed_input(client):
    project, _, source, reference = setup_data(client)
    project_id = project["id"]
    plan = definition(source["id"], reference["id"])
    preview = client.post("/api/pipelines/preview", json={"project_id": project_id,
        "name": "Harbor summary", "definition": plan}).json()
    assert preview["total_rows"] == 3 and preview["limit"] == 50
    assert {r["zone_zone_name"]: (r["orders"], r["total_amount"]) for r in preview["rows"]} == {
        "เหนือ": (3, 350.0), "ใต้": (1, 150.0), "ไม่ทราบ": (1, 400.0)}
    assert preview["join_diagnostics"][0]["unmatched_left_rows"] == 1
    saved = client.post("/api/pipelines", json={"project_id": project_id,
        "name": "Harbor summary", "definition": plan}).json()
    run = client.post(f"/api/pipelines/{saved['id']}/run").json()
    assert run["status"] == "queued"
    result = worker.execute_run(run["id"])
    assert result["status"] == "completed" and result["row_count"] == 3
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["version"] == 1 and detail["status"] == "completed"
    assert [s["row_count"] for s in detail["step"]] == [9, 8, 7, 6, 5, 5, 5, 5, 3, 3]
    parquet = client.get(f"/api/artifacts/{detail['output_artifact_id']}/download")
    assert parquet.status_code == 200
    sample_path = config.WORKSPACE / "tmp" / "verified_output.parquet"
    sample_path.write_bytes(parquet.content)
    with duckdb.connect() as con:
        rows = con.execute("SELECT zone_zone_name, orders, total_amount FROM read_parquet(?)",
                           [str(sample_path)]).fetchall()
    assert {name: (count, total) for name, count, total in rows} == {
        "เหนือ": (3, 350.0), "ใต้": (1, 150.0), "ไม่ทราบ": (1, 400.0)}
    plan["steps"][0]["columns"] = ["customer"]
    version = client.put(f"/api/pipelines/{saved['id']}", json={"name": "Harbor revised",
                         "definition": plan}).json()
    assert version["version"] == 2
    assert client.get(f"/api/pipelines/{saved['id']}").json()["name"] == "Harbor revised"
    rerun = client.post(f"/api/runs/{run['id']}/rerun").json()
    assert client.get(f"/api/runs/{rerun['id']}").json()["version"] == 1
    sql = "SELECT order_id, customer, amount FROM harbor_orders ORDER BY __source_row_id"
    for fmt in ("csv", "parquet"):
        job = client.post("/api/queries/export", json={"project_id": project_id, "sql": sql,
            "name": f"all_orders.{fmt}", "format": fmt, "utf8_bom": fmt == "csv"}).json()
        complete = worker.execute_export_job(job["id"])
        assert complete["status"] == "completed"
        data = client.get(f"/api/artifacts/{complete['artifact_id']}/download").content
        if fmt == "csv":
            assert data.startswith(b"\xef\xbb\xbf")
            records = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
            assert [(r["order_id"], r["amount"]) for r in records] == [
                ("H1", "100"), ("H2", "200"), ("H2", "250"), ("H3", "bad"),
                ("H4", "300"), ("H5", "400"), ("H6", ""), ("H7", "150"), ("H8", "50")]
            assert records[0]["customer"] == " กานต์ "
        else:
            export_path = config.WORKSPACE / "tmp" / "verified_export.parquet"
            export_path.write_bytes(data)
            with duckdb.connect() as con:
                assert con.execute("SELECT count(*) FROM read_parquet(?)", [str(export_path)]).fetchone()[0] == 9
    changed_project = client.post("/api/projects", json={"name": "Changed source"}).json()
    _, changed_source = import_file(client, changed_project["id"], "harbor_orders_changed.csv",
                                    "harbor_orders_changed", {"amount": "DOUBLE", "ordered_on": "DATE"})
    _, changed_ref = import_file(client, changed_project["id"], "zone_lookup.csv", "zone_lookup")
    changed_plan = definition(changed_source["id"], changed_ref["id"])
    changed_preview = client.post("/api/pipelines/preview", json={"project_id": changed_project["id"],
        "name": "Changed", "definition": changed_plan}).json()
    assert {r["zone_zone_name"]: r["total_amount"] for r in changed_preview["rows"]}["เหนือ"] == 370.0
    db.initialize_db()
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "completed"
    assert client.get(f"/api/projects/{project_id}/artifacts").json()


def test_duplicate_join_failure_and_running_cancel(client, monkeypatch):
    project, _, source, reference = setup_data(client)
    project_id = project["id"]
    _, duplicate = import_file(client, project_id, "zone_lookup_duplicate.csv", "zone_duplicates")
    plan = definition(source["id"], duplicate["id"])
    preview = client.post("/api/pipelines/preview", json={"project_id": project_id,
        "name": "Duplicate join", "definition": plan}).json()
    diagnostic = preview["join_diagnostics"][0]
    assert diagnostic["duplicate_reference_key_groups"] == 1
    assert diagnostic["unmatched_left_rows"] == 1
    assert preview["total_rows"] == 4
    strict_rule = client.post("/api/quality-rules", json={"project_id": project_id,
        "dataset_id": source["id"], "name": "Amount required", "kind": "required",
        "severity": "error", "config": {"column": "amount"}}).json()
    strict_plan = definition(source["id"], reference["id"])
    strict_plan["pre_rule_ids"] = [strict_rule["id"]]
    strict_plan["strict_quality"] = True
    failed_pipeline = client.post("/api/pipelines", json={"project_id": project_id,
        "name": "Strict failure", "definition": strict_plan}).json()
    failed_run = client.post(f"/api/pipelines/{failed_pipeline['id']}/run").json()
    failed = worker.execute_run(failed_run["id"])
    assert failed["status"] == "failed"
    assert client.get(f"/api/runs/{failed_run['id']}").json()["output_artifact_id"] is None
    cancel_pipeline = client.post("/api/pipelines", json={"project_id": project_id,
        "name": "Cancel in progress", "definition": definition(source["id"], reference["id"])}).json()
    live_run = client.post(f"/api/pipelines/{cancel_pipeline['id']}/run").json()
    entered = threading.Event()
    resume = threading.Event()
    original = worker._quality_checks
    def pause_during_work(*args, **kwargs):
        entered.set()
        assert resume.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, "_quality_checks", pause_during_work)
    thread = threading.Thread(target=worker.execute_run, args=(live_run["id"],))
    thread.start()
    try:
        assert entered.wait(10)
        assert client.get(f"/api/runs/{live_run['id']}").json()["status"] == "running"
        cancelled = client.post(f"/api/runs/{live_run['id']}/cancel").json()
        assert cancelled["cancellation_requested"]
    finally:
        resume.set()
        thread.join(10)
    assert not thread.is_alive()
    state = client.get(f"/api/runs/{live_run['id']}").json()
    assert state["status"] == "cancelled" and state["output_artifact_id"] is None
    assert not list((config.WORKSPACE / "tmp").glob(live_run["id"] + ".*"))


def test_remaining_pipeline_controls_execute_and_parquet_import(client):
    project, _, source, _ = setup_data(client)
    plan = {"source_dataset_id": source["id"], "pre_rule_ids": [], "post_rule_ids": [],
            "strict_quality": False, "output": {"format": "parquet", "filename": "other_steps.parquet"},
            "steps": [
                {"type": "select", "columns": ["order_id", "zone_code", "customer", "amount"]},
                {"type": "drop", "columns": ["customer"]},
                {"type": "rename", "mapping": {"zone_code": "zone"}},
                {"type": "case", "columns": ["zone"], "mode": "lower"},
                {"type": "filter", "conditions": [{"column": "amount", "op": "not_null"}]},
                {"type": "cast", "column": "amount", "target_type": "DOUBLE", "failure_policy": "drop"},
                {"type": "unknown_but_disabled", "enabled": False},
                {"type": "group", "group_by": ["zone"], "aggregations": [
                    {"function": "count", "as": "orders"},
                    {"function": "sum", "column": "amount", "as": "total_amount"}]},
                {"type": "sort", "by": [{"column": "zone", "direction": "asc"}]},
            ]}
    response = client.post("/api/pipelines/preview", json={"project_id": project["id"],
                           "name": "Other operations", "definition": plan})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert {r["zone"]: (r["orders"], r["total_amount"]) for r in preview["rows"]} == {
        "n": (4, 600.0), "s": (2, 450.0), "x": (1, 400.0)}
    assert preview["descriptions"][6]["enabled"] is False
    parquet_path = config.WORKSPACE / "tmp" / "thai_uploaded.parquet"
    with duckdb.connect() as con:
        con.execute("CREATE TABLE sample AS SELECT 1 AS id, 'เชียงใหม่' AS city UNION ALL SELECT 2, 'ภูเก็ต'")
        con.execute("COPY sample TO ? (FORMAT PARQUET)", [str(parquet_path)])
    stage = client.post("/api/imports/inspect", files={"file": ("thai_uploaded.parquet", parquet_path.read_bytes())},
                        data={"options": "{}"})
    assert stage.status_code == 200 and stage.json()["preview"]["row_count"] == 2
    committed = client.post("/api/imports/commit", json={"project_id": project["id"],
        "token": stage.json()["token"], "name": "Thai Parquet", "alias": "thai_parquet"})
    assert committed.status_code == 200, committed.text
    rows = client.get(f"/api/datasets/{committed.json()['id']}/rows").json()["rows"]
    assert [(r["id"], r["city"]) for r in rows] == [(1, "เชียงใหม่"), (2, "ภูเก็ต")]


def test_late_failure_does_not_publish_completed_output(client, monkeypatch):
    project, _, source, reference = setup_data(client)
    plan = definition(source["id"], reference["id"])
    pipeline = client.post("/api/pipelines", json={"project_id": project["id"],
                           "name": "Late publication failure", "definition": plan}).json()
    # Force a failure after COPY and file rename, while metadata publication is pending.
    monkeypatch.setattr(worker, "join_diagnostics", lambda *_: [{"invalid": object()}])
    run = client.post(f"/api/pipelines/{pipeline['id']}/run").json()
    result = worker.execute_run(run["id"])
    assert result["status"] == "failed"
    assert "not JSON serializable" in result["error"]
    assert client.get(f"/api/runs/{run['id']}").json()["output_artifact_id"] is None
    assert client.get(f"/api/projects/{project['id']}/artifacts").json() == []
    assert not list((config.WORKSPACE / "tmp").glob(run["id"] + ".*"))
    assert not list((config.WORKSPACE / "exports").iterdir())


def test_export_cancel_before_publication_removes_unregistered_file(client, monkeypatch):
    project, _, _, _ = setup_data(client)
    job = client.post("/api/queries/export", json={"project_id": project["id"],
        "sql": "SELECT * FROM harbor_orders", "name": "cancelled.csv", "format": "csv"}).json()
    real_export = worker.export_sql
    def cancel_after_copy(*args, **kwargs):
        artifact = real_export(*args, **kwargs)
        with db.connect() as con:
            con.execute("UPDATE export_jobs SET cancel_requested=1 WHERE id=?", (job["id"],))
        return artifact
    monkeypatch.setattr(worker, "export_sql", cancel_after_copy)
    result = worker.execute_export_job(job["id"])
    assert result["status"] == "cancelled"
    assert client.get(f"/api/export-jobs/{job['id']}").json()["artifact_id"] is None
    assert client.get(f"/api/projects/{project['id']}/artifacts").json() == []
    assert not list((config.WORKSPACE / "exports").iterdir())
