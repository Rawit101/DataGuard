# DataGuard real-data verification results

Date: 15 October 2026 (Asia/Bangkok). Repository: `DataGuard`, starting HEAD `d201cdf5f599583d87569b3bda9b37355b83af40`. The working tree already contained uncommitted fixes; these were preserved. No commit or push was made.

## Acceptance results

The final verification uses actual CSV files, real browser requests, the actual FastAPI/DuckDB processing and worker, SQLite records, and real downloaded artifacts. Expected fixture values were independently derived using Python csv/Counter. No fake success responses or hardcoded dashboard values were introduced.

| Check | Actual final result | Status |
|---|---|---|
| Backend pytest | 89 passed; one existing Starlette/AnyIO deprecation warning | PASS |
| Frontend unit tests | 2 passed | PASS |
| ESLint | Exit0, no findings | PASS |
| TypeScript + production build | Exit0; Vite production bundle generated | PASS |
| Chromium regular browser suite | 27 passed; 2 restart-only cases deferred to controlled restart phase | PASS |
| Chromium actual restart suite | 2 passed, including executing all five saved pipelines again | PASS |
| WebKit regular browser suite | 27 passed; same 2 cases deferred | PASS |
| WebKit actual restart suite | 2 passed | PASS |
| Direct SQLite + file assertions | Both browser workspaces passed | PASS |
| Metadata + stored-file persistence | Every table row and raw/data/export SHA-256 equal across actual server restart | PASS |
| Original user workspace | All13 tables and all25 stored-file hashes unchanged | PASS |
| Dependency consistency | `pip check`: no broken requirements | PASS |
| Changes whitespace check | `git diff --check`: exit0 | PASS |
| Native Safari/Firefox/Windows | Not executed | NOT TESTED |

Final automated test executions: **149 passed, 0 failed**: 89 backend +2 frontend unit +58 browser executions. Each regular browser run discovers29 cases, executes27 and defers2; the controlled driver then executes those2 after an actual process restart. They are not left unverified. Quality rules deliberately producing FAILED and blocked pipeline runs are expected data outcomes, not failed tests.

Earlier attempts are retained separately: the first new browser pass had25 passed/2 failed/2 deferred, and the new backend regressions initially exposed2 duplicate-policy failures and1 encoding-error failure. These were diagnosed and repaired, then affected checks and the broader suites were rerun. The test harness corrections are documented in [BUG_FIX_REPORT.md](BUG_FIX_REPORT.md).

## Observed data results

| Independent expectation | Actual backend/export result |
|---|---|
| Customers6 rows | 6 exact records, including NULL provinces and repeated Mali record |
| Total amount13,000 | SQL SUM13,000; AVG2166.6666666666665 |
| Distinct customer IDs5 | COUNT DISTINCT5 |
| amount>1500 rows4 | Mali, Mali, Nida, Suda after name ordering |
| Bangkok rows2 | Anan/Somchai |
| Missing province2 | Required rule:6 evaluated,2 failed,33.3%; downloaded failure rows4/5 |
| Extra duplicate customer ID1 | Explicit beyond-first rule:1 failed,16.7%, source row5 |
| All-group uniqueness2 | Legacy/default policy:2 failed; no saved-rule semantic changes |
| Positive amount0 failures | PASSED with Error severity explicitly separated |
| Filter + deduplicate3 rows | Suda2500/North, Mali3000/NULL, Nida2000/South |
| Seven transformation steps | Row counts6→4→3→3→3→3→3 |
| CSV + Parquet outputs | Every field and source/reference lineage value compared; format-specific numeric/null representation checked |
| Large fixture50,000 rows | SQL and directly decoded stored Parquet:50,000 rows, amount sum24,836,625 |
| Thai CSV export | ลูกค้า_50000 and ลูกค้า_49999 preserved; requested UTF-8 BOM verified |
| Quality gates | Pre and post Error failures prevented publication; warning failure + passing post rule completed |
| Overview | UI6 datasets/4 pipelines/4 recent runs matched API and SQLite; second project0/0/0 |

CSV storage intentionally preserves raw values as VARCHAR, even when a numeric declared type is inferred. Consequently the SQL SUM/AVG examples use explicit CAST; `SUM(amount)` without a cast is not claimed to succeed on raw CSV text. Parquet output preserves types. This is an existing data-preservation contract, not a new mock or an automatic conversion.

## Isolation and persistence evidence

Original workspace: `work/full-system-workspace`. Before testing, the SQLite database was backed up, every table was read without writes, and SHA-256 hashes of all raw/data/export files were recorded. It contained6 projects,8 datasets,8 pipelines,10 pipeline versions,8 pipeline runs,2 quality runs and9 artifacts. Every record in all13 tables and all25 stored files matched after restoration. No regression projects were added there.

Each driver creates a new temporary workspace prefixed `dataguard-real-chromium-` or `dataguard-real-webkit-`, refuses to reuse port8000, then verifies `/api/settings.workspace` before running mutations. The frontend and backend share the same isolated server. Test paths, actual server PIDs and counts are in each `result.json`. Snapshot comparison occurs immediately after restart, before new saved-pipeline executions add legitimate run history. Artifact downloads after restart also match their pre-restart SHA-256 hashes.

The main acceptance project contains6 datasets,4 pipelines and4 execution records:2 successful pipelines and2 intentionally quality-blocked pipelines. The separate five-pipeline regression project contains all five required names, a sentinel and an independent duplicate. Other existing suite scenarios have their own projects within the disposable workspace. The driver leaves temporary data available for diagnosis and terminates only its own server/worker.

Local evidence (generated by the reproduction commands and intentionally excluded from Git):

- Chromium full log: `work/real-data-e2e-evidence/chromium/browser-before-restart.log`, restart log: `work/real-data-e2e-evidence/chromium/browser-after-restart.log`, result: `work/real-data-e2e-evidence/chromium/result.json`.
- WebKit full log: `work/real-data-e2e-evidence/webkit/browser-before-restart.log`, restart log: `work/real-data-e2e-evidence/webkit/browser-after-restart.log`, result: `work/real-data-e2e-evidence/webkit/result.json`.
- Direct DB assertions: `work/real-data-e2e-evidence/chromium/database-assertions.json`, before snapshot: `work/real-data-e2e-evidence/chromium/database-before-restart.json`, after snapshot: `work/real-data-e2e-evidence/chromium/database-after-restart.json`.
- Original workspace preservation: `work/real-data-e2e-evidence/user-preservation.json`.
- API inventory: `work/real-data-e2e-evidence/api-inventory.md`:44 actual route operations including the frontend catch-all. OpenAPI JSON is captured per browser.
- Browser telemetry JSON contains request methods/paths/statuses, failed browser requests, console errors and page errors. Main success flows assert no unexpected API or console errors. Expected security400/403/404/409, real conversion errors and offline failures are recorded separately.
- Backend logs are captured before/after restart. Expected quality-gate failures reside in actual run records; no unexpected server traceback was observed in the successful runs.

Existing race tests delay delivery of actual backend responses without replacing their data. Two pre-existing reliability/layout tests intentionally inject HTTP500 to verify error presentation. They are explicitly supplementary fault-injection tests; core real-data success, real conversion-error and offline-recovery acceptance tests do not intercept routes.

## Architecture and supported operations discovered

React19.2/TypeScript/Vite7 frontend; FastAPI/Pydantic2 backend; Python3.12.14, Node22.23.2, npm10.9.8, DuckDB1.4.2, Playwright1.56.1 on macOS. SQLite schema3 stores projects, datasets, import rejects, saved queries/query runs, quality rules/runs, pipelines/versions/runs, artifacts/export jobs and schema metadata. Files live under workspace raw/data/exports/tmp directories.

The7 pages are Overview, Datasets, SQL Workspace, Data Quality, Pipeline Builder, Runs & Exports, Settings & Help. Import/profile,8 quality kinds,14 pipeline operations, version management, SQL saves/exports, cancellation, rerun and downloads are implemented. Project rename and saved-query/rule editing/deletion are supported. Project/dataset/pipeline deletion is not exposed. Settings are read-only startup configuration. Authentication, multi-user authorization, schedulers, external database connectors and Spark are not implemented.

The three overview KPIs trace from Overview.tsx to GET project overview, then SQLite datasets/pipelines/runs queries. Counts use returned array lengths. Recent runs is explicitly latest10, not an all-time execution count. There are no project-total, quality-rate, chart or other dashboard KPI displays to validate.

Environment settings inspected: DATAGUARD_WORKSPACE, DATAGUARD_MAX_UPLOAD_BYTES, DATAGUARD_QUERY_TIMEOUT_SECONDS, DATAGUARD_MEMORY_LIMIT and internal test/worker flags. Environment example has configuration defaults, no credentials; reports record only relevant nonsecret settings. Loopback binding, TrustedHost, mutation Origin guard, approved-alias SQL validation and managed path containment are tested. Requests without Origin remain permitted for local CLI/API clients. This is not a hostile multi-user sandbox.

## Reproduce

From the repository root, with the normal app stopped and port8000 free:

```sh
# Existing setup, if dependencies are not installed:
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
cd frontend
PLAYWRIGHT_BROWSERS_PATH=../work/playwright-browsers npx playwright install chromium webkit
cd ..

.venv/bin/python scripts/audit/generate_real_data.py
.venv/bin/python -m pytest -q backend/tests
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run build

PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright-browsers" .venv/bin/python scripts/audit/real_data_system.py --browser chromium --evidence work/real-data-e2e-evidence/chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright-browsers" .venv/bin/python scripts/audit/real_data_system.py --browser webkit --evidence work/real-data-e2e-evidence/webkit

git diff --check

# Restore the existing workspace after the driver has stopped its own server:
DATAGUARD_WORKSPACE="$PWD/work/full-system-workspace" .venv/bin/python scripts/start.py
# Stop the foreground app normally with Ctrl-C.
```

The driver starts `.venv/bin/python scripts/start.py` with its own environment, polls health/settings, runs Playwright with DATAGUARD_E2E_EXTERNAL_SERVER=1, terminates/waits the actual subprocess, starts a new subprocess against the same directory, compares records/hashes, runs post-restart browser tests, and terminates its server in a finally block. It never kills an arbitrary process or automatically stops the user's server.

No new runtime dependency was required. Existing Python packages pass pip check. The first macOS process inspection required the usual process permission; browser/server execution succeeded with the necessary local process access. No requested check remains BLOCKED.

## Exact files changed for this request

Application: `backend/app/quality.py`, `backend/app/imports.py`, `frontend/src/Quality.tsx`.

Tests and runners: `backend/tests/test_real_data_contract.py`, `frontend/tests/real-data-system.spec.ts`, `scripts/audit/generate_real_data.py`, `scripts/audit/read_export.py`, `scripts/audit/real_data_system.py`.

Fixtures: `tests/fixtures/e2e/customers.csv`, `provinces.csv`, `invalid_types.csv`, `empty_dataset.csv`, `duplicate_records.csv`, `large_dataset.csv`, `malformed.csv`, `zero_bytes.csv`, `invalid_encoding.csv`, `unsupported.txt`, `expected.json`, `manifest.json` (all in that directory).

Documentation: `docs/E2E_TEST_PLAN.md`, `docs/E2E_TEST_RESULTS.md`, `docs/BUG_FIX_REPORT.md`, `docs/FEATURE_VERIFICATION_MATRIX.md`, `docs/API.md`, `docs/UserGuide.md`, `README.md`.

Generated logs, screenshots, downloads, snapshots and backups remain ignored under `work/`; no browser binaries/dependencies/build outputs were added to source tracking. Existing unrelated uncommitted changes are listed separately in the initial Git status and were preserved. No database schema/migration change was made by this request.

## Remaining limitations

- WebKit passed; native Safari was not run. Firefox, Windows and other OS combinations remain untested.
- HTTP resource-limit tests deliberately use a4 MiB upload cap and2-second timeout. Default256 MiB/30-second boundary stress and peak memory are not measured; the application defaults are unchanged. The real load fixture is50,000 rows (~1.7 MiB); no larger new throughput claim is made.
- The targeted secret scan checks known key/private-key signatures in inspected responses and evidence logs. It does not establish an exhaustive secret audit or authenticate local clients.
- Unsaved pipeline drafts are not persisted. Version names are pipeline-level; historical names cannot be reconstructed. Historical definitions remain viewable/runnable/duplicable/restorable. Quality execution errors remain transient UI/log errors rather than completed quality calculation history.
- Stored CSV snapshots retain raw text. Use CAST for numeric SQL. A uniqueness pair can correctly mean2 affected rows or1 extra occurrence; select the explicit policy you intend.
- Settings editing, login/roles, resource deletion APIs noted above and extra dashboard charts/metrics are NOT SUPPORTED. No unavailable external credentials/services were required for supported local features.
- One existing dependency deprecation warning remains. Local port8000 must be free for the reproducible driver.

The application is restored at http://127.0.0.1:8000 with the original saved workspace.

## Re-verification 21 October 2026

The suite above was re-run against the current branch `clean-source` after commit `a3cf22c`, and Firefox coverage was added. `scripts/audit/real_data_system.py` now accepts `--browser firefox`; Playwright Firefox 142.0.1 was installed under `work/playwright-browsers`.

| Check | Actual result | Status |
|---|---|---|
| Backend pytest | 89 passed; same single Starlette/AnyIO deprecation warning | PASS |
| Frontend unit tests | 2 passed | PASS |
| ESLint and production build | Both exit 0 | PASS |
| Chromium real-data suite | 27 passed + 2 passed after an actual restart | PASS |
| WebKit real-data suite | 27 passed + 2 passed after an actual restart | PASS |
| Firefox real-data suite | 27 passed + 2 passed after an actual restart | PASS |
| Per-browser SQLite/file assertions | PASS for all three engines: raw bytes and SHA-256 exact, 50,000-row Parquet sum 24,836,625, overview 6/4/4 | PASS |
| Restart persistence | Metadata rows and stored-file hashes identical; downloaded artifacts match pre-restart hashes | PASS |
| `pip check` and `git diff --check` | Clean | PASS |
| Original `work/full-system-workspace` | No file modified; every driver used a temporary workspace | PASS |

Totals: 89 backend + 2 frontend unit + 3 × 29 browser executions = 178 passed, 0 failed. The earlier 15 October run remains valid for Chromium and WebKit.

Native Safari and Windows remain untested; Firefox coverage above is the Playwright Firefox engine on macOS, not a manual native-browser pass.
