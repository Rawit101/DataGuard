import json
from pathlib import Path

import duckdb

from backend.app import config, db
from backend.app.worker import execute_export_job, execute_run


def test_complete_sample_workflow_and_restart(client, demo):
    project_id = demo["project_id"]
    datasets = client.get(f"/api/projects/{project_id}/datasets").json()
    assert {d["alias"] for d in datasets} == {"property_listings", "province_master"}
    assert next(d for d in datasets if d["alias"] == "property_listings")["row_count"] == 12
    rules = client.get(f"/api/projects/{project_id}/quality-rules").json()
    failures = {r["name"]: client.post(f"/api/quality-rules/{r['id']}/run").json()["failed_rows"] for r in rules}
    assert failures == {"Listing ID required": 0, "Listing ID unique": 2, "Price parses": 1,
                        "Date parses": 1, "Province exists": 1}
    detail = client.get(f"/api/pipelines/{demo['pipeline_id']}").json()
    preview = client.post("/api/pipelines/preview", json={"project_id": project_id,
                           "name": detail["name"], "definition": detail["definition"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["total_rows"] == 5
    assert preview.json()["join_diagnostics"][0]["unmatched_left_rows"] == 1
    queued = client.post(f"/api/pipelines/{demo['pipeline_id']}/run").json()
    completed = execute_run(queued["id"])
    assert completed["status"] == "completed", completed
    artifact = client.get(f"/api/artifacts/{completed['artifact_id']}/download")
    assert artifact.status_code == 200 and len(artifact.content) > 100
    with db.connect() as con:
        path = con.execute("SELECT path FROM artifacts WHERE id=?", (completed["artifact_id"],)).fetchone()[0]
    data = duckdb.connect().execute("SELECT province_province_name, listing_count, avg_price_per_sqm FROM read_parquet(?) ORDER BY 1", [path]).fetchall()
    assert data == [("Bangkok", 2, 75000.0), ("Chonburi", 2, 50000.0),
                    ("Nonthaburi", 1, 50000.0), ("Samut Prakan", 1, 100000.0),
                    ("Unknown", 1, 80000.0)]
    # A new metadata connection, like a restarted app, sees all saved work.
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM pipeline_versions").fetchone()[0] == 1
        assert con.execute("SELECT status FROM runs WHERE id=?", (queued["id"],)).fetchone()[0] == "completed"


def test_sql_is_limited_to_approved_data(client, demo):
    project_id = demo["project_id"]
    valid = ["SELECT count(*) AS n FROM property_listings",
             "WITH p AS (SELECT province_code FROM property_listings) SELECT count(*) FROM p",
             "SELECT listing_id, row_number() OVER (ORDER BY listing_id) FROM property_listings"]
    for sql in valid:
        response = client.post("/api/queries/run", json={"project_id": project_id, "sql": sql})
        assert response.status_code == 200, response.text
    forbidden = ["SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM '/etc/passwd'",
                 "SELECT * FROM missing", "SELECT * FROM property_listings; DELETE FROM property_listings",
                 "COPY property_listings TO '/tmp/leak.csv'", "SELECT * FROM glob('/etc/*')"]
    for sql in forbidden:
        response = client.post("/api/queries/run", json={"project_id": project_id, "sql": sql})
        assert response.status_code == 400, (sql, response.text)


def test_malformed_csv_and_parse_failure_remain_visible(client):
    project_id = client.post("/api/projects", json={"name": "Import check"}).json()["id"]
    raw = b"id,value\n1,10\n2,not-a-number\n3,30,extra\n4,40\n"
    inspected = client.post("/api/imports/inspect", files={"file": ("broken.csv", raw, "text/csv")},
                            data={"options": "{}"})
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["preview"]["issue_count"] == 1
    committed = client.post("/api/imports/commit", json={"project_id": project_id,
                            "token": inspected.json()["token"], "name": "Broken", "type_overrides": {"value": "BIGINT"}})
    assert committed.status_code == 200, committed.text
    dataset_id = committed.json()["id"]
    assert committed.json()["row_count"] == 3
    assert committed.json()["issue_count"] == 2
    rows = client.get(f"/api/datasets/{dataset_id}/rows").json()["rows"]
    assert [r["__source_row_id"] for r in rows] == [1, 2, 4]
    assert rows[1]["value"] == "not-a-number"
    issues = client.get(f"/api/datasets/{dataset_id}/issues").json()["rows"]
    assert any("Expected 2 columns" in r["reason"] for r in issues)
    assert any("Cannot parse" in r["reason"] for r in issues)
    with db.connect() as con:
        raw_path = Path(con.execute("SELECT raw_path FROM datasets WHERE id=?", (dataset_id,)).fetchone()[0])
    assert raw_path.read_bytes() == raw


def test_profile_uses_declared_types_for_summaries(client, demo):
    listings = next(d for d in client.get(f"/api/projects/{demo['project_id']}/datasets").json()
                    if d["alias"] == "property_listings")
    # The fixture is inferred as text because it includes deliberately invalid values.
    # Import overrides should make profile min/max numeric or temporal, without losing bad values.
    with db.connect() as con:
        schema = json.loads(con.execute("SELECT schema_json FROM datasets WHERE id=?", (listings["id"],)).fetchone()[0])
        for column in schema:
            if column["name"] == "price_thb":
                column["declared_type"] = "DOUBLE"
            if column["name"] == "listed_at":
                column["declared_type"] = "DATE"
        con.execute("UPDATE datasets SET schema_json=? WHERE id=?", (json.dumps(schema), listings["id"]))
    profile = client.get(f"/api/datasets/{listings['id']}/profile")
    assert profile.status_code == 200, profile.text
    columns = {item["name"]: item for item in profile.json()["columns"]}
    assert columns["price_thb"]["min"] == 2000000.0
    assert columns["price_thb"]["max"] == 9000000.0
    assert columns["price_thb"]["invalid_count"] == 1
    assert columns["listed_at"]["max"] == "2026-01-12"
    assert columns["listed_at"]["invalid_count"] == 1


def test_unrecoverable_csv_tail_is_recorded(client):
    project_id = client.post("/api/projects", json={"name": "Broken quote"}).json()["id"]
    raw = b'id,value\n1,ok\n2,"unterminated\n3,still-present\n'
    inspected = client.post("/api/imports/inspect", files={"file": ("quote.csv", raw, "text/csv")},
                            data={"options": "{}"})
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["preview"]["issue_count"] == 1
    committed = client.post("/api/imports/commit", json={"project_id": project_id,
                            "token": inspected.json()["token"], "name": "Quote"})
    assert committed.status_code == 200, committed.text
    assert committed.json()["row_count"] == 1
    issue = client.get(f"/api/datasets/{committed.json()['id']}/issues").json()["rows"][0]
    assert "remaining input rejected" in issue["reason"]
    assert "3,still-present" in issue["raw_line"]


def test_pipeline_version_and_cancel_cleanup(client, demo):
    pipeline_id = demo["pipeline_id"]
    original = client.get(f"/api/pipelines/{pipeline_id}").json()["definition"]
    changed = json.loads(json.dumps(original))
    changed["steps"].append({"type": "sort", "by": [{"column": "listing_count", "direction": "desc"}]})
    saved = client.put(f"/api/pipelines/{pipeline_id}", json={"definition": changed})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    with db.connect() as con:
        v1 = json.loads(con.execute("SELECT definition_json FROM pipeline_versions WHERE pipeline_id=? AND version=1", (pipeline_id,)).fetchone()[0])
    assert v1 == original
    queued = client.post(f"/api/pipelines/{pipeline_id}/run?version=1").json()
    cancelled = client.post(f"/api/runs/{queued['id']}/cancel")
    assert cancelled.status_code == 200
    assert client.get(f"/api/runs/{queued['id']}").json()["status"] == "cancelled"
    assert not client.get(f"/api/projects/{demo['project_id']}/artifacts").json()


def test_csv_bom_export_and_thai_roundtrip(client):
    project_id = client.post("/api/projects", json={"name": "Thai data"}).json()["id"]
    raw = "id,province,price\n1,กรุงเทพมหานคร,100\n2,ชลบุรี,\n".encode("utf-8-sig")
    stage = client.post("/api/imports/inspect", files={"file": ("thai.csv", raw, "text/csv")},
                        data={"options": "{}"}).json()
    dataset = client.post("/api/imports/commit", json={"project_id": project_id, "token": stage["token"],
                          "name": "Thai", "alias": "thai"}).json()
    assert client.get(f"/api/datasets/{dataset['id']}/rows").json()["rows"][0]["province"] == "กรุงเทพมหานคร"
    for fmt in ("csv", "parquet"):
        job = client.post("/api/queries/export", json={"project_id": project_id,
                          "sql": "SELECT id, province, price FROM thai", "name": f"thai.{fmt}",
                          "format": fmt, "utf8_bom": fmt == "csv"}).json()
        completed = execute_export_job(job["id"])
        assert completed["status"] == "completed", completed
        downloaded = client.get(f"/api/artifacts/{completed['artifact_id']}/download")
        assert downloaded.status_code == 200
        if fmt == "csv":
            assert downloaded.content.startswith(b"\xef\xbb\xbf")
            assert "กรุงเทพมหานคร" in downloaded.content.decode("utf-8-sig")
        else:
            with db.connect() as con:
                path = con.execute("SELECT path FROM artifacts WHERE id=?", (completed["artifact_id"],)).fetchone()[0]
            assert duckdb.connect().execute("SELECT province FROM read_parquet(?) WHERE id='1'", [path]).fetchone()[0] == "กรุงเทพมหานคร"
