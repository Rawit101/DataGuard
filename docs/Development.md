# Development guide

Use Python 3.12 and Node 22. Run `python scripts/dev.py` after installing dependencies as shown in the README. It builds with Vite and serves the UI and API through FastAPI on one URL. `python scripts/start.py` serves an already built frontend. For optional Vite hot reload, run `npm run dev` in `frontend/` alongside the API on port 8000; its proxy routes `/api` to FastAPI. Keep test data in `demo/` or temporary test workspaces; do not commit personal files or `.dataguard/`.

## Extend a quality rule

Add its kind to `quality.KINDS`, validate its configuration and column names in `rule_condition`, and implement row-level or dataset-level evaluation in `evaluate_rule`. Add an editor template to `frontend/src/Quality.tsx`. Cover null behavior, failing row count, examples, history, and failure download in a backend test.

## Extend a transformation

Add a typed case to `compile_pipeline` in `pipeline.py`; validate the incoming schema, update the outgoing schema, and emit a human-readable description and the SQL actually executed. Add a step template to `frontend/src/PipelineBuilder.tsx`. Test preview and full-run equivalence, row identity behavior, and any failure policy. Avoid accepting raw SQL expressions in step configuration.

## Add a connector or engine

Keep the import boundary producing an immutable managed snapshot and metadata record (schema, hash, stable ID, project, alias). A future connector should use explicit credentials and location restrictions rather than weakening the SQL query connection. An alternate engine such as PySpark can consume the versioned step definition and produce a plan while preserving the worker's status, quality, lineage, and artifact contract. Arbitrary Python execution is outside this MVP.

## Checks and migrations

Install test tools with `.venv/bin/python -m pip install -r backend/requirements-dev.txt`. Run `.venv/bin/python -m pytest -q backend/tests` and, from `frontend`, `npm run test`, `npm run lint`, `npm run build`, and `npm run e2e` where the browser is available. The real-data driver covers Chromium, WebKit and Firefox: `.venv/bin/python scripts/audit/real_data_system.py --browser firefox`. Backend tests use an isolated `DATAGUARD_WORKSPACE`. Update `db.py` and increment `schema_version` for metadata changes, adding a forward migration rather than modifying earlier installed workspaces in place. Use immutable pipeline versions for definition changes.

## Backup

`scripts/backup.py` creates a verified workspace archive using the sqlite3 backup API, so it is consistent while the app is running. Keep its output under `work/` (ignored by Git) or another external location. See [Backup and restore](Backup.md).
