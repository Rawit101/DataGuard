from __future__ import annotations

import json
import signal
import threading
import time

from .config import managed_path
from .db import connect, initialize_db, new_id, now, one
from .engine import data_connection, export_sql, json_value
from .imports import quote_ident, sql_literal
from .pipeline import compile_pipeline, join_diagnostics
from .quality import evaluate_rule


STOP = False


def _stop(*_args):
    global STOP
    STOP = True


def _cancel_requested(run_id: str) -> bool:
    with connect() as db:
        row = db.execute("SELECT cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()
        return bool(row and row[0])


def _check_cancel(run_id: str) -> None:
    if _cancel_requested(run_id):
        raise InterruptedError("Run cancelled")


def _watch_cancel(run_id: str, con, done: threading.Event) -> None:
    while not done.wait(0.25):
        if _cancel_requested(run_id):
            con.interrupt()
            return


def _quality_checks(con, project_id: str, rule_ids: list[str], alias: str, columns: set[str]) -> list[dict]:
    results = []
    for rule_id in rule_ids:
        with connect() as db:
            rule = one(db, "SELECT * FROM quality_rules WHERE id=? AND project_id=?", (rule_id, project_id))
        results.append(evaluate_rule(con, rule, alias, columns, project_id, persist=False))
    return results


def execute_run(run_id: str) -> dict:
    with connect() as db:
        run = one(db, "SELECT * FROM runs WHERE id=?", (run_id,))
        pipeline = one(db, "SELECT * FROM pipelines WHERE id=?", (run["pipeline_id"],))
        version = one(db, "SELECT * FROM pipeline_versions WHERE pipeline_id=? AND version=?",
                      (run["pipeline_id"], run["version"]))
        datasets = [dict(x) for x in db.execute("SELECT * FROM datasets WHERE project_id=?", (pipeline["project_id"],))]
    definition = json.loads(version["definition_json"])
    compiled = compile_pipeline(pipeline["project_id"], definition)
    artifact_id = new_id()
    output_format = definition.get("output", {}).get("format", "parquet")
    if output_format not in {"csv", "parquet"}:
        raise ValueError("Invalid output format")
    tmp = managed_path("tmp", run_id + "." + output_format)
    final = managed_path("exports", artifact_id + "." + output_format)
    bom_tmp = managed_path("tmp", run_id + ".bom.csv")
    started = time.monotonic()
    quality = []
    steps = []
    done = threading.Event()
    with connect() as db:
        db.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (now(), run_id))
    try:
        _check_cancel(run_id)
        with data_connection(pipeline["project_id"], [tmp]) as con:
            watcher = threading.Thread(target=_watch_cancel, args=(run_id, con, done), daemon=True)
            watcher.start()
            try:
                source = next(d for d in datasets if d["id"] == definition["source_dataset_id"])
                source_cols = {c["name"] for c in json.loads(source["schema_json"])} | {"__source_row_id"}
                quality.extend(_quality_checks(con, pipeline["project_id"], definition.get("pre_rule_ids", []),
                                               source["alias"], source_cols))
                if definition.get("strict_quality", True) and any(
                    r["status"] == "failed" and r["severity"] == "error" for r in quality):
                    raise ValueError("Pre-run error-severity quality check failed")
                for item in compiled["descriptions"]:
                    _check_cancel(run_id)
                    if item["enabled"]:
                        preview_sql = compile_pipeline(pipeline["project_id"], definition,
                                                       stop_after=item["index"])["sql"]
                        count = con.execute(f"SELECT count(*) FROM ({preview_sql})").fetchone()[0]
                    else:
                        count = None
                    steps.append({**item, "row_count": count, "status": "completed"})
                    with connect() as db:
                        db.execute("UPDATE runs SET step_json=? WHERE id=?", (json.dumps(steps), run_id))
                con.execute(f"CREATE VIEW __pipeline_output AS {compiled['sql']}")
                quality.extend(_quality_checks(con, pipeline["project_id"], definition.get("post_rule_ids", []),
                                               "__pipeline_output", set(compiled["columns"])))
                if definition.get("strict_quality", True) and any(
                    r["status"] == "failed" and r["severity"] == "error" for r in quality):
                    raise ValueError("Post-run error-severity quality check failed")
                _check_cancel(run_id)
                count = con.execute("SELECT count(*) FROM __pipeline_output").fetchone()[0]
                diagnostics = join_diagnostics(con, pipeline["project_id"], definition, compiled)
                copy_options = "FORMAT CSV, HEADER TRUE" if output_format == "csv" else "FORMAT PARQUET"
                con.execute(f"COPY __pipeline_output TO {sql_literal(tmp)} ({copy_options})")
            finally:
                done.set()
                watcher.join(timeout=1)
        _check_cancel(run_id)
        if output_format == "csv" and definition.get("output", {}).get("utf8_bom", False):
            with bom_tmp.open("wb") as out, tmp.open("rb") as source:
                out.write(b"\xef\xbb\xbf")
                while chunk := source.read(1024 * 1024):
                    out.write(chunk)
            tmp.unlink()
            bom_tmp.replace(tmp)
        tmp.replace(final)
        relevant = [d for d in datasets if d["id"] in compiled["source_ids"]]
        lineage = {"inputs": [{"dataset_id": d["id"], "sha256": d["sha256"]} for d in relevant],
                   "pipeline_id": pipeline["id"], "version": run["version"], "output_artifact_id": artifact_id,
                   "join_diagnostics": diagnostics,
                   "join_semantics": "Joined rows retain left and reference source IDs; aggregate rows show source count."}
        name = definition.get("output", {}).get("filename", f"{pipeline['name']}.{output_format}")
        if not name or "/" in name or "\\" in name:
            raise ValueError("Invalid output filename")
        with connect() as db:
            if db.execute("SELECT cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()[0]:
                raise InterruptedError("Run cancelled before output publication")
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                       (artifact_id, pipeline["project_id"], run_id, "pipeline_output", output_format,
                        str(final), name, count, now()))
            db.execute("UPDATE runs SET status='completed', finished_at=?, duration_ms=?, step_json=?, quality_json=?, "
                       "lineage_json=?, output_artifact_id=? WHERE id=?",
                       (now(), int((time.monotonic()-started)*1000), json.dumps(steps),
                        json.dumps(quality, default=json_value), json.dumps(lineage), artifact_id, run_id))
        return {"status": "completed", "artifact_id": artifact_id, "row_count": count}
    except Exception as exc:
        status = "cancelled" if _cancel_requested(run_id) or isinstance(exc, InterruptedError) else "failed"
        with connect() as db:
            db.execute("UPDATE runs SET status=?, finished_at=?, duration_ms=?, step_json=?, quality_json=?, error=? WHERE id=?",
                       (status, now(), int((time.monotonic()-started)*1000), json.dumps(steps),
                        json.dumps(quality, default=json_value), str(exc), run_id))
        final.unlink(missing_ok=True)
        return {"status": status, "error": str(exc)}
    finally:
        tmp.unlink(missing_ok=True)
        bom_tmp.unlink(missing_ok=True)


def execute_export_job(job_id: str) -> dict:
    with connect() as db:
        job = one(db, "SELECT * FROM export_jobs WHERE id=?", (job_id,))
        if job["status"] != "queued":
            return {"status": job["status"]}
        db.execute("UPDATE export_jobs SET status='running', started_at=? WHERE id=?", (now(), job_id))
    def cancelled():
        with connect() as db:
            row = db.execute("SELECT cancel_requested FROM export_jobs WHERE id=?", (job_id,)).fetchone()
            return bool(row and row[0])
    artifact = None
    try:
        artifact = export_sql(job["project_id"], job["sql_text"], job["name"], job["format"],
                              bool(job["utf8_bom"]), cancel_check=cancelled)
        with connect() as db:
            if db.execute("SELECT cancel_requested FROM export_jobs WHERE id=?", (job_id,)).fetchone()[0]:
                raise InterruptedError("Export cancelled before publication")
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                       (artifact["id"], artifact["project_id"], None, "sql_export", artifact["format"],
                        artifact["path"], artifact["name"], artifact["row_count"], now()))
            db.execute("UPDATE export_jobs SET status='completed', finished_at=?, artifact_id=? WHERE id=?",
                       (now(), artifact["id"], job_id))
        return {"status": "completed", "artifact_id": artifact["id"]}
    except Exception as exc:
        status = "cancelled" if cancelled() or isinstance(exc, InterruptedError) else "failed"
        with connect() as db:
            if artifact is not None:
                db.execute("DELETE FROM artifacts WHERE id=?", (artifact["id"],))
            db.execute("UPDATE export_jobs SET status=?, finished_at=?, error=? WHERE id=?",
                       (status, now(), str(exc), job_id))
        if artifact is not None:
            managed_path("exports", artifact["id"] + "." + artifact["format"]).unlink(missing_ok=True)
        return {"status": status, "error": str(exc)}


def run_forever() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    initialize_db()
    while not STOP:
        with connect() as db:
            row = db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            try:
                execute_run(row[0])
            except Exception as exc:
                with connect() as db:
                    db.execute("UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?",
                               (now(), str(exc), row[0]))
        else:
            with connect() as db:
                export_row = db.execute("SELECT id FROM export_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if export_row:
                execute_export_job(export_row[0])
            else:
                time.sleep(0.3)


if __name__ == "__main__":
    run_forever()
