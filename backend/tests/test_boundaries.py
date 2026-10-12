import json
from pathlib import Path

import duckdb

from backend.app import config, db, main, worker
from backend.app.engine import data_connection
from backend.app.pipeline import compile_pipeline


def test_cast_and_duplicate_policies(client, demo):
    project_id = demo["project_id"]
    source_id = demo["dataset_ids"][0]
    definition = {"source_dataset_id": source_id, "steps": [
        {"type": "cast", "column": "price_thb", "target_type": "DOUBLE", "failure_policy": "null"},
        {"type": "deduplicate", "keys": ["listing_id"], "order_by": "__source_row_id", "keep": "last"}],
        "pre_rule_ids": [], "post_rule_ids": [], "strict_quality": False, "output": {"format": "parquet"}}
    sql = compile_pipeline(project_id, definition)["sql"]
    with data_connection(project_id) as con:
        result = con.execute(f"SELECT price_thb FROM ({sql}) WHERE listing_id='L001'").fetchone()
        assert result[0] == 9000000.0
        assert con.execute(f"SELECT count(*) FROM ({sql}) WHERE price_thb IS NULL").fetchone()[0] == 2
    definition["steps"][0]["failure_policy"] = "error"
    sql = compile_pipeline(project_id, definition)["sql"]
    with data_connection(project_id) as con:
        try:
            con.execute(f"SELECT sum(price_thb) FROM ({sql})").fetchone()
            assert False, "Invalid numeric text must fail an error-policy cast"
        except duckdb.ConversionException:
            pass


def test_duplicate_reference_keys_warn_before_row_multiplication(client, demo):
    project_id = demo["project_id"]
    raw = (main.ROOT / "demo/province_master_duplicate.csv").read_bytes()
    stage = client.post("/api/imports/inspect", files={"file": ("duplicate.csv", raw, "text/csv")},
                        data={"options": "{}"}).json()
    ref = client.post("/api/imports/commit", json={"project_id": project_id, "token": stage["token"],
                      "name": "Duplicate reference", "alias": "duplicate_reference"}).json()
    definition = {"source_dataset_id": demo["dataset_ids"][0], "steps": [
        {"type": "join", "reference_dataset_id": ref["id"], "join_type": "left",
         "keys": [{"left": "province_code", "right": "province_code"}], "prefix": "dup_",
         "cardinality": "many_to_one"}], "pre_rule_ids": [], "post_rule_ids": [],
         "strict_quality": False, "output": {"format": "parquet"}}
    response = client.post("/api/pipelines/preview", json={"project_id": project_id,
                           "name": "Join check", "definition": definition})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total_rows"] == 17
    assert result["join_diagnostics"][0]["duplicate_reference_key_groups"] == 1
    assert result["join_diagnostics"][0]["warning"]


def test_strict_quality_failure_does_not_publish_output(client, demo):
    project_id = demo["project_id"]
    source_id = demo["dataset_ids"][0]
    rule = client.post("/api/quality-rules", json={"project_id": project_id, "dataset_id": source_id,
                       "name": "Price required", "kind": "required", "severity": "error",
                       "config": {"column": "price_thb"}}).json()
    definition = {"source_dataset_id": source_id, "steps": [], "pre_rule_ids": [rule["id"]],
                  "post_rule_ids": [], "strict_quality": True,
                  "output": {"format": "parquet", "filename": "should_not_exist.parquet"}}
    pipeline = client.post("/api/pipelines", json={"project_id": project_id, "name": "Strict",
                            "definition": definition}).json()
    run = client.post(f"/api/pipelines/{pipeline['id']}/run").json()
    result = worker.execute_run(run["id"])
    assert result["status"] == "failed"
    assert "quality check failed" in result["error"]
    assert client.get(f"/api/runs/{run['id']}").json()["output_artifact_id"] is None
    assert client.get(f"/api/projects/{project_id}/artifacts").json() == []


def test_interrupted_and_path_boundaries(client, demo, monkeypatch, tmp_path):
    project_id = demo["project_id"]
    pipeline_id = demo["pipeline_id"]
    run = client.post(f"/api/pipelines/{pipeline_id}/run").json()
    with db.connect() as con:
        con.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
    db.initialize_db()
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "interrupted"
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    with db.connect() as con:
        con.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                    ("f" * 32, project_id, None, "sql_export", "csv", str(outside), "outside.csv", 1, db.now()))
    assert client.get(f"/api/artifacts/{'f'*32}/download").status_code == 404
    try:
        config.managed_path("exports", "../outside.txt")
        assert False, "Traversal must fail"
    except ValueError:
        pass


def test_mid_run_cancellation_cleans_temporary_output(client, demo, monkeypatch):
    run = client.post(f"/api/pipelines/{demo['pipeline_id']}/run").json()
    original = worker._check_cancel
    calls = 0
    def interrupt(run_id):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise InterruptedError("Run cancelled")
        return original(run_id)
    monkeypatch.setattr(worker, "_check_cancel", interrupt)
    result = worker.execute_run(run["id"])
    assert result["status"] == "cancelled"
    assert client.get(f"/api/runs/{run['id']}").json()["output_artifact_id"] is None
    assert not list((config.WORKSPACE / "tmp").glob(run["id"] + ".*"))
