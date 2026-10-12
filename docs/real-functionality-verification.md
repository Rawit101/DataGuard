# Real functionality verification

Verified with fresh `backend/tests/fixtures/harbor_orders.csv`, `harbor_orders_changed.csv`, `zone_lookup.csv`, and `zone_lookup_duplicate.csv`, separate from the bundled real estate example. The browser run used a new **Harbor browser verified** project in an isolated workspace; existing user data was not changed.

## Independently calculated fixture outcomes

The orders file has 9 accepted records and one malformed six-field record. Declaring `amount` as DOUBLE and `ordered_on` as DATE adds two parse issues while keeping `bad` and `not-a-date` as raw values. `H2` occurs twice, `H5` has an unmatched zone `X`, and `H6` has null customer and amount.

The saved transformation trims text, removes missing amount, drops invalid amount/date casts, keeps the first `H2`, left joins the zone lookup, fills the unmatched name with `ไม่ทราบ`, calculates tax, then groups and sorts. Step row counts are exactly `9, 8, 7, 6, 5, 5, 5, 5, 3, 3`. The final Parquet contains:

| Zone | Orders | Total amount |
| --- | ---: | ---: |
| เหนือ | 3 | 350 |
| ใต้ | 1 | 150 |
| ไม่ทราบ | 1 | 400 |

Changing `H8` amount from 50 to 70 in a different input file changes the northern total to 370. A duplicate `N` reference key produces one duplicate-key group, one unmatched left row, and four final groups because matched `N` rows multiply before aggregation. These values were asserted from downloaded data and API results, not screenshots.

## Checks by feature

| Feature | Real execution evidence | Result / limit |
| --- | --- | --- |
| Projects and empty states | Browser created, renamed, switched to an empty project, switched back, reloaded, then survived a server stop/start; API also reopened SQLite metadata. | Passed. The empty project showed no inherited datasets. |
| CSV and Parquet imports | Browser uploaded the new Thai CSV and reference CSV. API compared retained raw bytes, parsed issues, accepted rows, and generated Thai Parquet import. | Passed: 9 accepted, 3 issues; 2 Parquet rows. |
| Explorer | Browser displayed nine real rows, profile and issue tabs. API asserted filtering, paging, sorting, schema, null and invalid counts. | Passed; amount valid min 50, max 400, one null, one invalid. |
| SQL and persistence | Browser aggregate query returned `N=5,S=3,X=1`; save/reopen worked. Invalid table showed an error and no stale result. API blocked a cross-project alias. | Passed. |
| Quality | Browser rule found source row 4 (`H3`, `bad`). API asserted duplicate rows 2 and 3, invalid date row 5, unmatched zone row 6, and full violation CSVs. Rule edits persisted their selected dataset. | Passed. |
| Transformations and preview | Browser configured ten steps, previewed three exact totals and unmatched `X`. API covered select, drop, rename, trim, case, fill, drop-missing, cast, deduplicate, filter, calculate, join, group, sort, and disabled steps. | Passed. Preview shows at most 50 by default, after full computation. |
| Versioned pipelines and runs | Browser saved v1, saw queued then completed, ten step logs and Parquet output. API saved v2, renamed pipeline, reran v1 and checked its recorded version. | Passed. |
| Cancellation and failed output | API held a worker after it reached `running`, cancelled via `/runs/{id}/cancel`, then verified `cancelled`, no output ID and no temporary file. Strict quality failure and a late output-name failure published no artifact. SQL export cancellation after COPY removed the unregistered file. | Passed for running state and cleanup. A long DuckDB statement interruption itself was not exercised. |
| CSV and Parquet export | Browser exported all nine source rows to CSV with UTF-8 BOM and produced a three-row pipeline Parquet. API independently parsed full CSV and Parquet downloads. | Passed, including Thai text, nulls and invalid raw values. |
| Restart and errors | Stopped and relaunched the local server using the same isolated workspace. Browser still showed two datasets, one pipeline, one completed run and artifacts. Browser console had no warnings/errors; server log had expected 400 for rejected SQL and no error traceback. | Passed. A stale local project ID initially caused a 404 before loading; the loading-order fix removed it on restart. |

## Automated checks

- Backend: `.venv/bin/python -m pytest -q backend/tests` → **18 passed**. One Starlette/AnyIO deprecation warning from an installed dependency.
- Frontend unit: `npm run test` → **2 passed**.
- Type check and production build: `npm run build` → **passed**. ESLint: `npm run lint` → **passed**.
- Playwright `npm run e2e`: **blocked before tests execute**. The Chromium binary was installed in an isolated cache, then macOS rejected its Mach port registration: `MachPortRendezvousServer: Permission denied (1100)`. No browser test was counted as passing, and no security protection was disabled. The in-app browser workflow above ran successfully through the real UI.

## Reproduce with your files

From the DataGuard directory, after installing dependencies as described in `README.md`, run `python scripts/dev.py` and open `http://127.0.0.1:8000`. Create and name a project; import your CSV or Parquet files; inspect rows, schema, profile and import issues; run a SELECT in SQL Workspace; create and run quality rules; build a pipeline, preview it, save a version, and choose **Run saved version**. In Runs & Exports, review status and step counts, then download the output. Export a full SQL result from SQL Workspace when you need its untransformed rows. Stop and restart with the same `DATAGUARD_WORKSPACE` path to reopen all saved work.

To rerun the independent checks locally, use the commands above. For Playwright, install Chromium with `cd frontend && npx playwright install chromium`, then run `npm run e2e` while port 8000 is free. On a host where Chromium can launch, the two browser tests should execute; their status remains unverified here.
