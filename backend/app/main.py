from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .config import MAX_PREVIEW_ROWS, MAX_UPLOAD_BYTES, QUERY_TIMEOUT_SECONDS, ROOT, WORKSPACE, managed_path
from .db import connect, initialize_db, many, new_id, now, one
from .engine import dataset_page, export_sql, profile_dataset, project_datasets, run_user_sql
from .imports import ImportOptions, commit_import, inspect_file, safe_alias, stage_upload
from .pipeline import compile_pipeline, join_diagnostics
from .quality import KINDS, SEVERITIES, rule_condition, run_rule, validate_rule_for_dataset


worker_process = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global worker_process
    initialize_db()
    if os.environ.get("DATAGUARD_NO_WORKER") != "1":
        worker_process = subprocess.Popen([sys.executable, "-m", "backend.app.worker"], cwd=ROOT,
                                          env=os.environ.copy())
    yield
    if worker_process is not None:
        worker_process.terminate()
        try:
            worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_process.kill()
            worker_process.wait()


app = FastAPI(title="DataGuard API", version="0.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:5173", "http://localhost:5173",
                                     "http://127.0.0.1:8000", "http://localhost:8000"}:
            return PlainTextResponse("Origin not allowed", status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(_request: Request, exc: ValueError):
    return PlainTextResponse(str(exc), status_code=400)


@app.exception_handler(duckdb.Error)
async def duckdb_error(_request: Request, exc: duckdb.Error):
    return PlainTextResponse(str(exc), status_code=400)


@app.exception_handler(KeyError)
async def key_error(_request: Request, exc: KeyError):
    return PlainTextResponse(str(exc), status_code=404)


@app.exception_handler(sqlite3.IntegrityError)
async def constraint_error(_request: Request, _exc: sqlite3.IntegrityError):
    return PlainTextResponse("Record conflicts with existing data. Refresh and retry with a new record ID.", status_code=409)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CommitIn(BaseModel):
    project_id: str
    token: str
    name: str = Field(min_length=1, max_length=100)
    alias: str | None = None
    type_overrides: dict[str, str] = Field(default_factory=dict)


class SqlIn(BaseModel):
    project_id: str
    sql: str
    page: int = 1
    page_size: int = 50


class SavedQueryIn(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    sql: str


class ExportIn(BaseModel):
    project_id: str
    sql: str
    name: str
    format: str = "parquet"
    utf8_bom: bool = False


class RuleIn(BaseModel):
    project_id: str
    dataset_id: str
    name: str = Field(min_length=1, max_length=100)
    kind: str
    severity: str = "error"
    config: dict


class PipelineIn(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    project_id: str
    name: str = Field(min_length=1, max_length=100)
    definition: dict


class PipelineVersionIn(BaseModel):
    definition: dict
    name: str | None = Field(default=None, min_length=1, max_length=100)
    expected_current_version: int | None = Field(default=None, ge=1)


def validate_definition(project_id: str, definition: dict) -> dict:
    compiled = compile_pipeline(project_id, definition)
    source_id = definition.get("source_dataset_id")
    with connect() as db:
        source = one(db, "SELECT * FROM datasets WHERE id=? AND project_id=?", (source_id, project_id))
        source_columns = {c["name"] for c in json.loads(source["schema_json"])} | {"__source_row_id"}
        for stage, ids, columns in (("pre", definition.get("pre_rule_ids", []), source_columns),
                                    ("post", definition.get("post_rule_ids", []), set(compiled["columns"]))):
            for rule_id in ids:
                rule = one(db, "SELECT * FROM quality_rules WHERE id=? AND project_id=?", (rule_id, project_id))
                if stage == "pre" and rule["dataset_id"] != source_id:
                    raise ValueError("Pre-run rule must target the source dataset")
                rule_condition(rule, columns, project_id)
    from .engine import data_connection
    with data_connection(project_id) as con:
        con.execute(f"DESCRIBE SELECT * FROM ({compiled['sql']})")
    return compiled


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/settings")
def settings():
    with connect() as db:
        schema_version = int(one(db, "SELECT value FROM meta WHERE key='schema_version'")["value"])
    return {"workspace": str(WORKSPACE), "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_preview_rows": MAX_PREVIEW_ROWS, "query_timeout_seconds": QUERY_TIMEOUT_SECONDS,
            "local_only": True, "schema_version": schema_version}


@app.get("/api/projects")
def projects():
    with connect() as db:
        return many(db, "SELECT * FROM projects ORDER BY created_at DESC")


@app.post("/api/projects")
def create_project(payload: ProjectIn):
    name = payload.name.strip()
    if not name:
        raise ValueError("Project name cannot be blank")
    project_id = new_id()
    with connect() as db:
        db.execute("INSERT INTO projects VALUES (?,?,?)", (project_id, name, now()))
    return {"id": project_id, "name": name}


@app.put("/api/projects/{project_id}")
def rename_project(project_id: str, payload: ProjectIn):
    name = payload.name.strip()
    if not name:
        raise ValueError("Project name cannot be blank")
    with connect() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
        db.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
    return {"id": project_id, "name": name}


@app.get("/api/projects/{project_id}/overview")
def overview(project_id: str):
    with connect() as db:
        project = one(db, "SELECT * FROM projects WHERE id=?", (project_id,))
        datasets = many(db, "SELECT id,name,alias,row_count,reject_count,created_at FROM datasets WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        pipelines = many(db, "SELECT * FROM pipelines WHERE project_id=? ORDER BY updated_at DESC", (project_id,))
        runs = many(db, "SELECT r.* FROM runs r JOIN pipelines p ON p.id=r.pipeline_id WHERE p.project_id=? ORDER BY r.created_at DESC LIMIT 10", (project_id,))
    return {"project": project, "datasets": datasets, "pipelines": pipelines, "runs": runs}


@app.post("/api/imports/inspect")
async def inspect_upload(file: UploadFile = File(...), options: str = Form("{}")):
    parsed = ImportOptions.model_validate_json(options)
    return await stage_upload(file, parsed)


@app.post("/api/imports/commit")
def commit_upload(payload: CommitIn):
    return commit_import(payload.project_id, payload.token, payload.name, payload.alias, payload.type_overrides)


@app.get("/api/projects/{project_id}/datasets")
def datasets(project_id: str):
    result = project_datasets(project_id)
    for item in result:
        item["schema"] = json.loads(item.pop("schema_json"))
        item.pop("raw_path")
        item.pop("data_path")
    return result


@app.get("/api/datasets/{dataset_id}")
def dataset_detail(dataset_id: str):
    with connect() as db:
        item = one(db, "SELECT * FROM datasets WHERE id=?", (dataset_id,))
    item["schema"] = json.loads(item.pop("schema_json"))
    item["import_options"] = json.loads(item.pop("import_options_json"))
    item.pop("raw_path")
    item.pop("data_path")
    return item


@app.get("/api/datasets/{dataset_id}/rows")
def dataset_rows(dataset_id: str, page: int = 1, page_size: int = 50, sort: str | None = None,
                 direction: str = "asc", filter_column: str | None = None, filter_text: str | None = None):
    return dataset_page(dataset_id, page, page_size, sort, direction, filter_column, filter_text)


@app.get("/api/datasets/{dataset_id}/profile")
def dataset_profile(dataset_id: str):
    return profile_dataset(dataset_id)


@app.get("/api/datasets/{dataset_id}/issues")
def import_issues(dataset_id: str, page: int = 1, page_size: int = 50):
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("Invalid page")
    with connect() as db:
        one(db, "SELECT id FROM datasets WHERE id=?", (dataset_id,))
        rows = many(db, "SELECT * FROM import_rejects WHERE dataset_id=? LIMIT ? OFFSET ?",
                    (dataset_id, page_size, (page-1)*page_size))
        count = db.execute("SELECT count(*) FROM import_rejects WHERE dataset_id=?", (dataset_id,)).fetchone()[0]
    return {"rows": rows, "total_rows": count, "page": page}


@app.get("/api/projects/{project_id}/queries")
def saved_queries(project_id: str):
    with connect() as db:
        return many(db, "SELECT * FROM saved_queries WHERE project_id=? ORDER BY updated_at DESC", (project_id,))


@app.post("/api/queries/run")
def query(payload: SqlIn):
    return run_user_sql(payload.project_id, payload.sql, payload.page, payload.page_size)


@app.post("/api/queries")
def save_query(payload: SavedQueryIn):
    query_id = new_id()
    with connect() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (payload.project_id,))
        db.execute("INSERT INTO saved_queries VALUES (?,?,?,?,?,?)",
                   (query_id, payload.project_id, payload.name, payload.sql, now(), now()))
    return {"id": query_id}


@app.put("/api/queries/{query_id}")
def update_query(query_id: str, payload: SavedQueryIn):
    with connect() as db:
        one(db, "SELECT id FROM saved_queries WHERE id=? AND project_id=?", (query_id, payload.project_id))
        db.execute("UPDATE saved_queries SET name=?, sql_text=?, updated_at=? WHERE id=?",
                   (payload.name, payload.sql, now(), query_id))
    return {"id": query_id}


@app.delete("/api/queries/{query_id}")
def delete_query(query_id: str):
    with connect() as db:
        one(db, "SELECT id FROM saved_queries WHERE id=?", (query_id,))
        db.execute("DELETE FROM saved_queries WHERE id=?", (query_id,))
    return {"deleted": True}


@app.post("/api/queries/export")
def query_export(payload: ExportIn, request: Request):
    from .engine import validate_sql
    from .config import managed_path
    if payload.format not in {"csv", "parquet"} or not payload.name or Path(payload.name).name != payload.name:
        raise ValueError("Invalid export format or filename")
    validate_sql(payload.sql, {d["alias"].lower() for d in project_datasets(payload.project_id)})
    key = request.headers.get("Idempotency-Key")
    with connect() as db:
        if key:
            existing = db.execute("SELECT id,status,project_id,sql_text,name,format,utf8_bom FROM export_jobs WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if (existing[2], existing[3], existing[4], existing[5], existing[6]) != (payload.project_id, payload.sql, payload.name, payload.format, int(payload.utf8_bom)):
                    raise ValueError("Idempotency key belongs to a different export request")
                return {"id": existing[0], "status": existing[1]}
        job_id = new_id()
        db.execute("INSERT INTO export_jobs (id,project_id,sql_text,name,format,utf8_bom,status,created_at,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?)",
                   (job_id, payload.project_id, payload.sql, payload.name, payload.format,
                    int(payload.utf8_bom), "queued", now(), key))
    return {"id": job_id, "status": "queued"}


@app.get("/api/export-jobs/{job_id}")
def export_job(job_id: str):
    with connect() as db:
        return one(db, "SELECT * FROM export_jobs WHERE id=?", (job_id,))


@app.get("/api/projects/{project_id}/export-jobs")
def export_jobs(project_id: str):
    with connect() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
        return many(db, "SELECT id,project_id,name,format,status,error,artifact_id,created_at,started_at,finished_at "
                    "FROM export_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))


@app.post("/api/export-jobs/{job_id}/cancel")
def cancel_export(job_id: str):
    with connect() as db:
        job = one(db, "SELECT * FROM export_jobs WHERE id=?", (job_id,))
        requested = job["status"] in {"queued", "running"}
        if job["status"] == "queued":
            db.execute("UPDATE export_jobs SET status='cancelled', cancel_requested=1, finished_at=? WHERE id=?",
                       (now(), job_id))
        elif job["status"] == "running":
            db.execute("UPDATE export_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    return {"id": job_id, "cancellation_requested": requested}


@app.get("/api/projects/{project_id}/quality-rules")
def quality_rules(project_id: str):
    with connect() as db:
        rules = many(db, "SELECT * FROM quality_rules WHERE project_id=? ORDER BY created_at", (project_id,))
    for rule in rules:
        rule["config"] = json.loads(rule.pop("config_json"))
    return rules


@app.post("/api/quality-rules")
def create_rule(payload: RuleIn):
    if payload.kind not in KINDS or payload.severity not in SEVERITIES:
        raise ValueError("Unsupported rule kind or severity")
    validate_rule_for_dataset(payload.kind, payload.config, payload.dataset_id, payload.project_id)
    rule_id = new_id()
    with connect() as db:
        one(db, "SELECT id FROM datasets WHERE id=? AND project_id=?", (payload.dataset_id, payload.project_id))
        db.execute("INSERT INTO quality_rules VALUES (?,?,?,?,?,?,?,?)",
                   (rule_id, payload.project_id, payload.dataset_id, payload.name, payload.kind,
                    payload.severity, json.dumps(payload.config), now()))
    return {"id": rule_id}


@app.put("/api/quality-rules/{rule_id}")
def update_rule(rule_id: str, payload: RuleIn):
    if payload.kind not in KINDS or payload.severity not in SEVERITIES:
        raise ValueError("Unsupported rule kind or severity")
    validate_rule_for_dataset(payload.kind, payload.config, payload.dataset_id, payload.project_id)
    with connect() as db:
        one(db, "SELECT id FROM quality_rules WHERE id=? AND project_id=?", (rule_id, payload.project_id))
        one(db, "SELECT id FROM datasets WHERE id=? AND project_id=?", (payload.dataset_id, payload.project_id))
        db.execute("UPDATE quality_rules SET dataset_id=?, name=?, kind=?, severity=?, config_json=? WHERE id=?",
                   (payload.dataset_id, payload.name, payload.kind, payload.severity,
                    json.dumps(payload.config), rule_id))
    return {"id": rule_id}


@app.delete("/api/quality-rules/{rule_id}")
def delete_rule(rule_id: str):
    with connect() as db:
        rule = one(db, "SELECT project_id FROM quality_rules WHERE id=?", (rule_id,))
        versions = many(db, "SELECT v.definition_json FROM pipeline_versions v JOIN pipelines p "
                        "ON p.id=v.pipeline_id WHERE p.project_id=?", (rule["project_id"],))
        for version in versions:
            definition = json.loads(version["definition_json"])
            if rule_id in definition.get("pre_rule_ids", []) or rule_id in definition.get("post_rule_ids", []):
                raise ValueError("Rule is referenced by a saved pipeline version and cannot be deleted")
        db.execute("DELETE FROM quality_runs WHERE rule_id=?", (rule_id,))
        db.execute("DELETE FROM quality_rules WHERE id=?", (rule_id,))
    return {"deleted": True}


@app.post("/api/quality-rules/{rule_id}/run")
def execute_quality(rule_id: str):
    return run_rule(rule_id)


@app.get("/api/quality-rules/{rule_id}/runs")
def quality_history(rule_id: str):
    with connect() as db:
        one(db, "SELECT id FROM quality_rules WHERE id=?", (rule_id,))
        rows = many(db, "SELECT * FROM quality_runs WHERE rule_id=? ORDER BY created_at DESC", (rule_id,))
    for row in rows:
        row["examples"] = json.loads(row["examples_json"])
        row["rule_snapshot"] = json.loads(row["rule_snapshot_json"]) if row["rule_snapshot_json"] else None
    return rows


@app.get("/api/projects/{project_id}/pipelines")
def pipelines(project_id: str):
    with connect() as db:
        one(db, "SELECT id FROM projects WHERE id=?", (project_id,))
        return many(db, "SELECT * FROM pipelines WHERE project_id=? ORDER BY updated_at DESC", (project_id,))


@app.post("/api/pipelines")
def create_pipeline(payload: PipelineIn):
    if not payload.name.strip():
        raise ValueError("Pipeline name cannot be blank")
    validate_definition(payload.project_id, payload.definition)
    pipeline_id = payload.id or new_id()
    with connect() as db:
        db.execute("INSERT INTO pipelines VALUES (?,?,?,?,?,?)",
                   (pipeline_id, payload.project_id, payload.name.strip(), 1, now(), now()))
        db.execute("INSERT INTO pipeline_versions VALUES (?,?,?,?)",
                   (pipeline_id, 1, json.dumps(payload.definition), now()))
    return {"id": pipeline_id, "version": 1}


@app.get("/api/pipelines/{pipeline_id}")
def pipeline_detail(pipeline_id: str, version: int | None = None):
    if version is not None and version < 1:
        raise ValueError("Version must be positive")
    with connect() as db:
        pipeline = one(db, "SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
        versions = many(db, "SELECT version,created_at FROM pipeline_versions WHERE pipeline_id=? ORDER BY version DESC", (pipeline_id,))
        current = one(db, "SELECT * FROM pipeline_versions WHERE pipeline_id=? AND version=?",
                      (pipeline_id, version if version is not None else pipeline["current_version"]))
    pipeline["definition"] = json.loads(current["definition_json"])
    pipeline["versions"] = versions
    pipeline["selected_version"] = current["version"]
    return pipeline


@app.put("/api/pipelines/{pipeline_id}")
def save_pipeline_version(pipeline_id: str, payload: PipelineVersionIn):
    with connect() as db:
        pipeline = one(db, "SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
    validate_definition(pipeline["project_id"], payload.definition)
    with connect() as db:
        # Allocate the next version under the same write lock as its insertion.
        db.execute("BEGIN IMMEDIATE")
        current = one(db, "SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
        if payload.expected_current_version is not None and payload.expected_current_version != current["current_version"]:
            raise HTTPException(409, "A newer version was saved elsewhere. Reload the pipeline before saving; your draft has been preserved.")
        name = payload.name.strip() if payload.name is not None else current["name"]
        if not name:
            raise ValueError("Pipeline name cannot be blank")
        version = current["current_version"] + 1
        db.execute("INSERT INTO pipeline_versions VALUES (?,?,?,?)",
                   (pipeline_id, version, json.dumps(payload.definition), now()))
        db.execute("UPDATE pipelines SET name=?, current_version=?, updated_at=? WHERE id=?",
                   (name, version, now(), pipeline_id))
    return {"id": pipeline_id, "version": version}


@app.post("/api/pipelines/{pipeline_id}/duplicate")
def duplicate_pipeline(pipeline_id: str, version: int | None = None):
    detail = pipeline_detail(pipeline_id, version)
    return create_pipeline(PipelineIn(project_id=detail["project_id"], name=detail["name"][:95] + " copy",
                                      definition=detail["definition"]))


@app.post("/api/pipelines/preview")
def preview_pipeline(payload: PipelineIn, stop_after: int | None = None, limit: int = 50):
    from .engine import data_connection, rows_as_dicts
    if limit < 1 or limit > MAX_PREVIEW_ROWS:
        raise ValueError("Preview limit out of range")
    validate_definition(payload.project_id, payload.definition)
    compiled = compile_pipeline(payload.project_id, payload.definition, stop_after)
    with data_connection(payload.project_id) as con:
        cursor = con.execute(f"SELECT * FROM ({compiled['sql']}) LIMIT ?", [limit])
        rows = rows_as_dicts(cursor, cursor.fetchall())
        total = con.execute(f"SELECT count(*) FROM ({compiled['sql']})").fetchone()[0]
        diagnostics = join_diagnostics(con, payload.project_id, payload.definition, compiled)
    return {**compiled, "rows": rows, "total_rows": total, "limit": limit,
            "join_diagnostics": diagnostics,
            "note": "The complete operation runs before the preview limit is applied."}


@app.post("/api/pipelines/script")
def pipeline_script(payload: PipelineIn):
    from .imports import quote_ident, sql_literal
    compiled = validate_definition(payload.project_id, payload.definition)
    datasets = {d["id"]: d for d in project_datasets(payload.project_id)}
    lines = ["-- DataGuard transformation SQL", "-- Run with DuckDB after adjusting file paths if the workspace has moved.",
             "-- The SQL below is the same transformation plan used for preview and full runs.",
             "-- Quality checks and atomic artifact publication are handled by DataGuard separately.", ""]
    for dataset_id in compiled["source_ids"]:
        ds = datasets[dataset_id]
        lines.append(f"CREATE OR REPLACE VIEW {quote_ident(ds['alias'])} AS SELECT * FROM read_parquet({sql_literal(ds['data_path'])});")
    lines.extend(["", compiled["sql"] + ";", ""])
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": "attachment; filename=pipeline.sql"})


@app.post("/api/pipelines/{pipeline_id}/run")
def queue_run(pipeline_id: str, request: Request, version: int | None = None):
    with connect() as db:
        pipeline = one(db, "SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
        if version is not None and version < 1:
            raise ValueError("Version must be positive")
        chosen = version if version is not None else pipeline["current_version"]
        one(db, "SELECT version FROM pipeline_versions WHERE pipeline_id=? AND version=?", (pipeline_id, chosen))
        key = request.headers.get("Idempotency-Key")
        if key:
            existing = db.execute("SELECT id,status,pipeline_id,version FROM runs WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if (existing[2], existing[3]) != (pipeline_id, chosen):
                    raise ValueError("Idempotency key belongs to a different pipeline run")
                return {"id": existing[0], "status": existing[1]}
        run_id = new_id()
        db.execute("INSERT INTO runs (id,pipeline_id,version,status,created_at,idempotency_key) VALUES (?,?,?,?,?,?)",
                   (run_id, pipeline_id, chosen, "queued", now(), key))
    return {"id": run_id, "status": "queued"}


@app.get("/api/projects/{project_id}/runs")
def runs(project_id: str):
    with connect() as db:
        return many(db, "SELECT r.*,p.name AS pipeline_name FROM runs r JOIN pipelines p ON p.id=r.pipeline_id "
                    "WHERE p.project_id=? ORDER BY r.created_at DESC", (project_id,))


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    with connect() as db:
        run = one(db, "SELECT * FROM runs WHERE id=?", (run_id,))
    for key in ("step_json", "quality_json", "lineage_json"):
        run[key.removesuffix("_json")] = json.loads(run.pop(key))
    return run


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    with connect() as db:
        run = one(db, "SELECT * FROM runs WHERE id=?", (run_id,))
        requested = run["status"] in {"queued", "running"}
        if run["status"] == "queued":
            db.execute("UPDATE runs SET status='cancelled', cancel_requested=1, finished_at=? WHERE id=?",
                       (now(), run_id))
        elif run["status"] == "running":
            db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
    return {"id": run_id, "cancellation_requested": requested}


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str, request: Request):
    with connect() as db:
        run = one(db, "SELECT * FROM runs WHERE id=?", (run_id,))
    return queue_run(run["pipeline_id"], request, run["version"])


@app.get("/api/projects/{project_id}/artifacts")
def artifacts(project_id: str):
    with connect() as db:
        return many(db, "SELECT id,project_id,run_id,kind,format,name,row_count,created_at FROM artifacts "
                    "WHERE project_id=? ORDER BY created_at DESC", (project_id,))


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str):
    with connect() as db:
        artifact = one(db, "SELECT * FROM artifacts WHERE id=?", (artifact_id,))
    expected = managed_path("exports", artifact_id + "." + artifact["format"])
    if Path(artifact["path"]).resolve() != expected or not expected.is_file():
        raise KeyError("Artifact not available")
    return FileResponse(expected, filename=artifact["name"], media_type="application/octet-stream")


def _stage_demo_file(path: Path) -> str:
    token = new_id()
    staged = managed_path("tmp", token + path.suffix)
    shutil.copy2(path, staged)
    opts = ImportOptions()
    state = {"token": token, "suffix": path.suffix, "original_name": path.name,
             "size": staged.stat().st_size, "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
             "options": opts.model_dump(), "preview": inspect_file(staged, opts)}
    managed_path("tmp", token + ".json").write_text(json.dumps(state, default=str), encoding="utf-8")
    return token


@app.post("/api/demo/load")
def load_demo():
    project = create_project(ProjectIn(name="Real estate demo"))
    source = commit_import(project["id"], _stage_demo_file(ROOT / "demo/property_listings.csv"),
                           "Property listings", "property_listings", {})
    ref = commit_import(project["id"], _stage_demo_file(ROOT / "demo/province_master.csv"),
                        "Province master", "province_master", {})
    rule_ids = []
    for name, kind, cfg in [
        ("Listing ID required", "required", {"column": "listing_id", "blank_is_missing": True}),
        ("Listing ID unique", "unique", {"columns": ["listing_id"]}),
        ("Price parses", "parse_valid", {"column": "price_thb", "type": "DOUBLE"}),
        ("Date parses", "parse_valid", {"column": "listed_at", "type": "DATE"}),
        ("Province exists", "foreign_key", {"column": "province_code", "reference_dataset_id": ref["id"],
                                               "reference_column": "province_code"}),
    ]:
        rule_ids.append(create_rule(RuleIn(project_id=project["id"], dataset_id=source["id"],
                                       name=name, kind=kind, severity="warning", config=cfg))["id"])
    definition = {"source_dataset_id": source["id"], "strict_quality": True, "pre_rule_ids": rule_ids,
                  "post_rule_ids": [], "output": {"format": "parquet", "filename": "property_summary.parquet"},
                  "steps": [
                      {"type": "trim", "columns": ["district", "province_code"]},
                      {"type": "drop_missing", "columns": ["price_thb"]},
                      {"type": "cast", "column": "price_thb", "target_type": "DOUBLE", "failure_policy": "drop"},
                      {"type": "cast", "column": "area_sqm", "target_type": "DOUBLE", "failure_policy": "drop"},
                      {"type": "cast", "column": "listed_at", "target_type": "DATE", "failure_policy": "drop"},
                      {"type": "filter", "conditions": [{"column": "area_sqm", "op": "gt", "value": 0}]},
                      {"type": "deduplicate", "keys": ["listing_id"], "order_by": "__source_row_id", "keep": "first"},
                      {"type": "join", "reference_dataset_id": ref["id"], "join_type": "left",
                       "keys": [{"left": "province_code", "right": "province_code"}], "prefix": "province_",
                       "cardinality": "many_to_one"},
                      {"type": "fill", "values": {"province_province_name": "Unknown"}},
                      {"type": "calculate", "as": "price_per_sqm", "expression": {"op": "divide",
                       "left": {"column": "price_thb"}, "right": {"column": "area_sqm"}}},
                      {"type": "group", "group_by": ["province_province_name"], "aggregations": [
                          {"function": "count", "as": "listing_count"},
                          {"function": "mean", "column": "price_per_sqm", "as": "avg_price_per_sqm"},
                          {"function": "sum", "column": "price_thb", "as": "total_price_thb"}]},
                      {"type": "sort", "by": [{"column": "province_province_name", "direction": "asc"}]},
                  ]}
    pipeline = create_pipeline(PipelineIn(project_id=project["id"], name="Clean and summarize listings",
                                          definition=definition))
    return {"project_id": project["id"], "dataset_ids": [source["id"], ref["id"]],
            "rule_ids": rule_ids, "pipeline_id": pipeline["id"]}


@app.get("/{path:path}")
def frontend(path: str):
    dist = ROOT / "frontend/dist"
    if not dist.is_dir():
        raise HTTPException(404, "Build the frontend or use the development launcher")
    candidate = (dist / path).resolve()
    if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
        return FileResponse(candidate)
    if path.startswith("api/") or path.startswith("assets/"):
        raise HTTPException(404, "Not found")
    return FileResponse(dist / "index.html")
