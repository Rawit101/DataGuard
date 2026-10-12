"""Cancel live DuckDB COPY work and verify that no partial artifact is published."""

import json
import time
from pathlib import Path

import httpx

WORKSPACE = Path(__file__).resolve().parents[2] / "work/full-system-workspace"
PROJECT = "b3be5161129443ca8d47b6514bd1d3fd"
DATASET = "590be63b07b44519ac75aba322e493ee"
BASE = "http://127.0.0.1:8000"


def check(response):
    assert response.status_code == 200, response.text
    return response.json()


def cancel_active(client, kind, job_id, suffix):
    path = f"/api/export-jobs/{job_id}" if kind == "export" else f"/api/runs/{job_id}"
    candidates = list((WORKSPACE / "tmp").glob(f"*.{suffix}"))
    original = {p.name for p in candidates}
    started = time.perf_counter()
    observed = None
    status = None
    for _ in range(400):
        status = check(client.get(path))["status"]
        new_files = [p for p in (WORKSPACE / "tmp").glob(f"*.{suffix}") if p.name not in original]
        if status == "running" and new_files:
            observed = {"temp_name": new_files[0].name, "temp_bytes_at_cancel": new_files[0].stat().st_size}
            break
        assert status not in {"failed", "completed", "cancelled"}, (kind, status)
        time.sleep(0.05)
    assert observed is not None, (kind, status, "COPY temp file not observed")
    cancel_path = f"/api/export-jobs/{job_id}/cancel" if kind == "export" else f"/api/runs/{job_id}/cancel"
    response = check(client.post(cancel_path))
    assert response["cancellation_requested"]
    cancelled_at = time.perf_counter()
    for _ in range(200):
        detail = check(client.get(path))
        if detail["status"] in {"cancelled", "failed", "completed"}:
            break
        time.sleep(0.05)
    assert detail["status"] == "cancelled", detail
    assert detail.get("artifact_id") is None and detail.get("output_artifact_id") is None
    assert not (WORKSPACE / "tmp" / observed["temp_name"]).exists()
    assert not any(a.get("run_id") == job_id for a in check(client.get(f"/api/projects/{PROJECT}/artifacts")))
    return {"kind": kind, "job_id": job_id, "status": detail["status"],
            "seconds_to_copy": round(cancelled_at-started, 3),
            "seconds_to_cancel": round(time.perf_counter()-cancelled_at, 3), **observed}


with httpx.Client(base_url=BASE, timeout=30) as client:
    export = check(client.post("/api/queries/export", json={"project_id": PROJECT,
        "sql": "SELECT a.order_id AS left_id, b.order_id AS right_id "
               "FROM audit_large a CROSS JOIN audit_large b LIMIT 100000000",
        "name": "cancel_active.csv", "format": "csv", "utf8_bom": False}))
    results = [cancel_active(client, "export", export["id"], "csv")]
    definition = {"source_dataset_id": DATASET, "pre_rule_ids": [], "post_rule_ids": [],
        "strict_quality": False, "output": {"format": "parquet", "filename": "cancel_active.parquet"},
        "steps": [{"type": "join", "reference_dataset_id": DATASET,
                   "join_type": "inner", "keys": [{"left": "grp", "right": "grp"}],
                   "prefix": "ref_", "cardinality": "many_to_many"}]}
    pipeline = check(client.post("/api/pipelines", json={"project_id": PROJECT,
        "name": "Active cancellation audit", "definition": definition}))
    run = check(client.post(f"/api/pipelines/{pipeline['id']}/run"))
    results.append(cancel_active(client, "pipeline", run["id"], "parquet"))
    print(json.dumps(results, indent=2))
