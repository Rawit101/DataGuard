# Real-data system verification plan — 15 October 2026

The application under test is the existing React 19 / TypeScript frontend served by FastAPI, with SQLite metadata schema 3 and managed CSV/Parquet storage. DuckDB runs approved queries with isolated connections. A single worker serializes pipeline and export processing. Existing changes are preserved in `work/real-data-e2e-evidence/existing-changes.patch` and the initial Git status is recorded beside it.

All mutation-heavy workflows run on loopback port 8000 with a newly created temporary `DATAGUARD_WORKSPACE`. The driver refuses an occupied port and asserts `/api/settings.workspace` matches that temporary directory before any writes. Production database backup, complete table snapshots, and SHA-256 hashes of stored raw/data/export files are captured before switching environments and compared after restoring the original app.

## Coverage

1. Generate physical CSVs under `tests/fixtures/e2e/`; independently derive expected values with Python csv/Counter (no DuckDB/application imports).
2. Create/rename two projects through the UI, upload all six required CSVs through the real chooser, inspect preview/schema/profile/issues, and confirm SQLite/file contents.
3. Execute SQL through the real editor, compare exact count/sum/avg/group/join/order/filter/limit values; reject syntax/unknown inputs/writes/file reads; check empty output and settings-derived limits.
4. Create/run real quality rules through the UI. Check required=2, duplicate occurrences=1 (explicit counting policy), positive=0, plus legacy all-group semantics, parse/range/accepted/regex/foreign-key/row-count checks, actual failure CSVs, percentages and history.
5. Save/run a multi-step typed/filter/deduplicate/select/sort/rename/join workflow; compare all columns and records in real CSV/Parquet downloads. Verify pre/post error gates block publication and warnings allow it.
6. Run five independent saved pipelines, immutable versions, duplicate, restore, concurrency conflict and refresh scenarios.
7. Reconcile every implemented overview KPI and recent-run row against SQLite/API. No unsupported KPI/chart claims.
8. Test local security boundaries: SQL, traversal, origin/host guards, cross-project references, invalid inputs, upload limits and actual unavailable-network retry. Settings are read-only; project/dataset/pipeline deletion and authentication are not implemented.
9. Stop/restart the actual isolated server process; compare every metadata row/file hash, reload projects/history/artifacts and execute five saved pipelines again.
10. Run backend/unit/lint/build and Chromium/WebKit/Firefox suites. Capture actual requests, response codes, browser errors, screenshots, backend logs, full database snapshots and parsed downloads. No substituted success responses.

## Limits

The isolated HTTP suite uses a 4 MiB upload cap and 2-second SQL timeout to test practical limits without uploading a 256 MiB boundary file. Defaults remain unchanged. Large fixture: 50,000 deterministic rows; the existing load driver additionally supports 1,000,000 rows. WebKit and Firefox are engine checks, not proof of native Safari or Firefox compatibility. Mutation requests without Origin remain allowed for local CLI/API clients; this is a local single-user application with no authentication.

## Planned commands

Run `scripts/audit/generate_real_data.py`, frontend unit/lint/build, `pytest backend/tests`, then `scripts/audit/real_data_system.py` with browser binaries supplied by `PLAYWRIGHT_BROWSERS_PATH`. Exact commands and actual outcomes will be recorded in E2E_TEST_RESULTS.md after execution. The driver always terminates its own server. The original saved workspace is restored separately using `DATAGUARD_WORKSPACE=... .venv/bin/python scripts/start.py`.
