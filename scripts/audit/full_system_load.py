"""Reproducible 100k-row live-server verification; all data stays in the audit workspace."""

import json
import resource
import time
from pathlib import Path

import duckdb
import httpx

ROOT = Path(__file__).resolve().parents[2] / "work"
DATA = ROOT / "full-system-fixtures"
DATA.mkdir(exist_ok=True)
SOURCE = DATA / "audit_large.parquet"
BASE = "http://127.0.0.1:8000"

with duckdb.connect() as con:
    con.execute("COPY (SELECT i AS order_id, i % 100 AS grp, i * 0.25 AS value, "
                "'customer_' || CAST(i % 1000 AS VARCHAR) AS customer "
                "FROM range(100000) AS t(i)) TO ? (FORMAT parquet)", [str(SOURCE)])

times = {}
with httpx.Client(base_url=BASE, timeout=90) as client:
    settings = client.get("/api/settings").json()
    assert settings["workspace"].endswith("work/full-system-workspace"), settings
    p = client.post("/api/projects", json={"name": "100k load audit"}).json()
    with SOURCE.open("rb") as handle:
        start = time.perf_counter()
        response = client.post("/api/imports/inspect", files={"file": (SOURCE.name, handle)},
                               data={"options": "{}"})
        times["inspect_seconds"] = round(time.perf_counter()-start, 3)
    assert response.status_code == 200, response.text
    stage = response.json()
    assert stage["preview"]["row_count"] == 100000
    start = time.perf_counter()
    response = client.post("/api/imports/commit", json={"project_id": p["id"],
        "token": stage["token"], "name": "Audit large", "alias": "audit_large"})
    times["commit_seconds"] = round(time.perf_counter()-start, 3)
    assert response.status_code == 200, response.text
    ds = response.json()
    assert ds["row_count"] == 100000
    start = time.perf_counter()
    page = client.get(f"/api/datasets/{ds['id']}/rows", params={"page": 501,
        "page_size": 100, "sort": "order_id", "direction": "desc"}).json()
    times["page_seconds"] = round(time.perf_counter()-start, 3)
    assert page["total_rows"] == 100000 and len(page["rows"]) == 100
    assert page["rows"][0]["order_id"] == 49999
    assert client.get(f"/api/datasets/{ds['id']}/rows", params={"page_size": 101}).status_code == 400
    start = time.perf_counter()
    profile = client.get(f"/api/datasets/{ds['id']}/profile").json()
    times["profile_seconds"] = round(time.perf_counter()-start, 3)
    assert profile["row_count"] == 100000
    sql = "SELECT grp, count(*) AS n FROM audit_large GROUP BY grp ORDER BY grp"
    start = time.perf_counter()
    query = client.post("/api/queries/run", json={"project_id": p["id"], "sql": sql,
        "page_size": 100}).json()
    times["sql_seconds"] = round(time.perf_counter()-start, 3)
    assert query["total_rows"] == 100 and len(query["rows"]) == 100
    assert all(row["n"] == 1000 for row in query["rows"])
    definition = {"source_dataset_id": ds["id"], "pre_rule_ids": [], "post_rule_ids": [],
        "strict_quality": False, "output": {"format": "parquet", "filename": "large_filtered.parquet"},
        "steps": [{"type": "filter", "conditions": [{"column": "order_id", "op": "gte", "value": 99000}]},
                  {"type": "sort", "by": [{"column": "order_id", "direction": "asc"}]}]}
    start = time.perf_counter()
    preview = client.post("/api/pipelines/preview", json={"project_id": p["id"],
        "name": "Large filtered", "definition": definition}).json()
    times["preview_seconds"] = round(time.perf_counter()-start, 3)
    assert preview["total_rows"] == 1000 and len(preview["rows"]) == 50
    pipeline = client.post("/api/pipelines", json={"project_id": p["id"],
        "name": "Large filtered", "definition": definition}).json()
    run = client.post(f"/api/pipelines/{pipeline['id']}/run").json()
    start = time.perf_counter()
    for _ in range(200):
        detail = client.get(f"/api/runs/{run['id']}").json()
        if detail["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.2)
    times["pipeline_seconds"] = round(time.perf_counter()-start, 3)
    assert detail["status"] == "completed", detail.get("error")
    assert [step["row_count"] for step in detail["step"]] == [1000, 1000]
    download = client.get(f"/api/artifacts/{detail['output_artifact_id']}/download")
    assert download.status_code == 200
    output = DATA / "large_filtered_result.parquet"
    output.write_bytes(download.content)
    with duckdb.connect() as con:
        assert con.execute("SELECT count(*), min(order_id), max(order_id) FROM read_parquet(?)",
                           [str(output)]).fetchone() == (1000, 99000, 99999)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    print(json.dumps({"source_bytes": SOURCE.stat().st_size,
        "pipeline_output_bytes": output.stat().st_size, "project_id": p["id"],
        "dataset_id": ds["id"], "pipeline_id": pipeline["id"], "times": times,
        "client_peak_rss_raw": usage.ru_maxrss,
        "client_cpu_user_seconds": round(usage.ru_utime, 3),
        "client_cpu_system_seconds": round(usage.ru_stime, 3)}, indent=2))
