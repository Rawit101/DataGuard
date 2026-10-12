from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import WORKSPACE, initialize_workspace


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid4().hex


@contextmanager
def connect():
    initialize_workspace()
    con = sqlite3.connect(WORKSPACE / "metadata.sqlite3", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datasets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 alias TEXT NOT NULL, original_name TEXT NOT NULL, raw_path TEXT NOT NULL, data_path TEXT NOT NULL,
 sha256 TEXT NOT NULL, size INTEGER NOT NULL, row_count INTEGER NOT NULL, reject_count INTEGER NOT NULL,
 schema_json TEXT NOT NULL, import_options_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id, alias)
);
CREATE TABLE IF NOT EXISTS import_rejects (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id), line INTEGER,
 column_name TEXT, raw_line TEXT, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_queries (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 sql_text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS query_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), sql_text TEXT NOT NULL,
 status TEXT NOT NULL, duration_ms INTEGER, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quality_rules (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), dataset_id TEXT NOT NULL REFERENCES datasets(id),
 name TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quality_runs (
 id TEXT PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES quality_rules(id), status TEXT NOT NULL,
 evaluated_rows INTEGER NOT NULL, failed_rows INTEGER NOT NULL, examples_json TEXT NOT NULL,
 artifact_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipelines (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 current_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_versions (
 pipeline_id TEXT NOT NULL REFERENCES pipelines(id), version INTEGER NOT NULL,
 definition_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(pipeline_id, version)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL REFERENCES pipelines(id), version INTEGER NOT NULL,
 status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, started_at TEXT, finished_at TEXT,
 duration_ms INTEGER, step_json TEXT NOT NULL DEFAULT '[]', quality_json TEXT NOT NULL DEFAULT '[]',
 lineage_json TEXT NOT NULL DEFAULT '{}', error TEXT, output_artifact_id TEXT, created_at TEXT NOT NULL,
 idempotency_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), run_id TEXT,
 kind TEXT NOT NULL, format TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
 row_count INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), sql_text TEXT NOT NULL,
 name TEXT NOT NULL, format TEXT NOT NULL, utf8_bom INTEGER NOT NULL, status TEXT NOT NULL,
 cancel_requested INTEGER NOT NULL DEFAULT 0, started_at TEXT, finished_at TEXT, error TEXT,
 artifact_id TEXT, created_at TEXT NOT NULL, idempotency_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_pipeline ON runs(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_rules_dataset ON quality_rules(dataset_id);
"""


def initialize_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            con.execute("INSERT INTO meta VALUES ('schema_version','2')")
        elif row[0] == "1":
            con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        elif row[0] != "2":
            raise RuntimeError(f"Unsupported metadata schema version: {row[0]}")
        con.execute("UPDATE runs SET status='interrupted', finished_at=?, error='App stopped during run' WHERE status='running'", (now(),))
        con.execute("UPDATE export_jobs SET status='interrupted', finished_at=?, error='App stopped during export' WHERE status='running'", (now(),))


def one(con: sqlite3.Connection, sql: str, params=()):
    row = con.execute(sql, params).fetchone()
    if row is None:
        raise KeyError("Record not found")
    return dict(row)


def many(con: sqlite3.Connection, sql: str, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]
