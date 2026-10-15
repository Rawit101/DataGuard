# Functional verification

Verified on 2026-10-13 (Asia/Bangkok), macOS, Python 3.12 and Node 22. All browser mutation tests used separate temporary workspaces. Saved user projects were not used as test fixtures.

## Actual results

| Check | Actual result |
| --- | --- |
| Backend unit/integration suite | **80 passed**, one upstream Starlette/AnyIO deprecation warning |
| Frontend unit tests | **2 passed** |
| Frontend lint | **PASS** |
| TypeScript and production Vite build | **PASS** |
| Playwright Chromium | **21 passed** (final run: 21.5 s) |
| Playwright WebKit on macOS | **21 passed** (final run: 21.9 s) |
| Dependency checks | `pip check`: no broken requirements; `npm ls --depth=0`: all declared dependencies installed |
| Repository whitespace check | `git diff --check`: **PASS** |
| Runtime inspection | No unexpected page errors/console errors in the new real-data audit flows or persisted-result screenshot flow; observed snapshot-server requests returned 200 and no backend traceback was recorded |
| Existing saved workspace | Schema 2→3, all table record counts unchanged, SHA-256 unchanged for all 14 original raw/snapshot files |

The first new backend regression run reproduced 11 failures. Those were fixed. The first full WebKit run found long-option mobile overflow; that was fixed. A keyboard assertion using native popup arrow keys was replaced with native type-ahead after an unstyled baseline reproduced the same headless macOS limitation. The final suites above contain no failing tests.

## Feature verification table

| Page / Feature | Original Problem | Fix Applied | Test Performed | Final Status |
| --- | --- | --- | --- | --- |
| Overview/project statistics | No hardcoded dataset or pipeline counts found; failed fetch could say no project was selected | Explicit loading/error/retry state | Actual project API counts, empty-state checks, seven-page error checks | PASS — Verified working with real data |
| Project create/rename/selection | Existing real API and SQLite operations | Preserved | Existing backend workflow checks and browser project creation/selection | PASS — Verified working with real data |
| CSV upload/ingestion | Incorrect multiline issue location and DECIMAL overflow validation | Physical lines and declared decimal bounds; raw data retained | Fresh browser CSV import; malformed field-count, parse, encoding, null, quoting, header-only and duplicate-header backend cases | PASS — Verified working with real data |
| Parquet import | Explicit overrides omitted parse issues; native numeric types lacked averages | Override issue scan and numeric type recognition | Native INTEGER/DECIMAL means, invalid raw value retained, native types/nonfinite previews, CSV/Parquet roundtrip | PASS — Verified working with real data |
| Dataset explorer | Late profile/issues could overwrite selected data; errors looked like loading | Response ownership, persistent errors and catalog loading | Real delayed profile switch; existing filter/sort/page tests; exact profile API assertions | PASS — Verified working with real data |
| SQL execution | Existing guarded backend; dataset-fetch and query failures were not persistently displayed | Dataset error/retry and query error panel; stale-query guard retained | Approved SQL, CTEs, empty results, invalid column, timeout, prohibited file/write/function operations; browser stale-query test | PASS — Verified working with real data |
| Saved SQL | Existing persisted queries | Preserved | Save/update/delete/reopen through browser and backend | PASS — Verified working with real data |
| Quality rules/validation | Malformed type settings could crash; unsupported patterns and bad bounds could be saved | Shape and DuckDB engine validation | All eight rule kinds run on real fixtures; bad type, bounds and regex cases rejected | PASS — Verified working with real data |
| Quality counts/reports | Dataset row-count failure displayed a misleading percentage | Distinct check/row labels and no dataset-level row percentage | Three-row fresh CSV: one missing customer, 33.3% failures; empty dataset minimum-1 check fails without showing 0% | PASS — Verified working with real data |
| Quality history/persistence | No execution snapshot; late previous-rule history could replace selection | Nullable snapshot migration, stored-result viewer, request revision guard | Run, rename/edit rule, reload, reopen original result and download; old-schema migration; delayed-history switch | PASS — Verified working with real data |
| Quality deletion | Deleting referenced rule broke saved pipeline versions | Reject referenced-rule deletion | Referenced delete returns 400; rule remains; unreferenced delete remains supported | PASS — Verified working with real data |
| Pipeline configuration/steps | Malformed/unexecutable plans could be saved; case-insensitive collisions | Shape/name/output validation and DuckDB binding before save | All 14 operations tested independently; fresh cleaning/join/aggregate UI; malformed configuration and collision regressions | PASS — Verified working with real data |
| Pipeline versions/duplicate | Concurrent version writes could collide | Transactional version allocation | Synchronized writes create versions 2 and 3; existing version and duplication tests | PASS — Verified working with real data |
| Pipeline loading/preview/SQL script | Download network error could escape handler | Handler reports errors; loading lock preserved | Delayed definition load disables editing; real full-plan preview and executable script download | PASS — Verified working with real data |
| Runs/step logs/quality/lineage | Late selected-run response could show the wrong details | Clear and guard selected detail; visible loading/error | Two actual completed jobs; delayed older detail cannot replace latest; backend exact step counts and lineage | PASS — Verified working with real data |
| Worker/cancellation/interruption | Existing serialized processing and atomic publication | Preserved; late-publication regression retained | Actual browser cancellation, queued/running cases, interrupted startup, failed strict quality and late-publication cleanup | PASS — Verified working with real data |
| CSV/Parquet export/download | Existing real exports | Preserved | Browser file downloads; CSV BOM bytes and full 120-row export; exact fixture totals and DuckDB Parquet readback in backend tests | PASS — Verified working with real data |
| Settings/help | Failed settings fetch stayed loading; schema response was a fixed value | Persistent error/retry; schema version from metadata | Injected failure, retry and actual schema/config comparison; navigation and sample workflow | PASS — Verified working with real data |
| Responsive/accessibility controls | WebKit long option enlarged mobile document | Native select sizing/layout containment | All seven pages at 375/768/1024/1440/1920 in both engines; long Thai names/columns, editor, inner table scroll, 125% CSS zoom check, native type-ahead selection | PASS — Verified working with real data |
| Authentication/user accounts | No account/authentication subsystem exists in this local MVP | No unrelated subsystem added | Source/route inventory confirms absence; loopback/SQL/path restrictions tested separately | NOT TESTED — Feature not implemented |
| External database/cloud sources | No connector subsystem exists | No credentials or fake connector added | Source/route/config inventory confirms absence | NOT TESTED — Feature not implemented |
| Scheduled monitoring/external alerts/global score/charts | These are not implemented UI/API features | Existing rule results and strict pipeline gates remain real | Supported rule/gate/report behavior tested; absent subsystems not claimed working | NOT TESTED — Feature not implemented |
| Installed Safari/Firefox/Windows | Not exercised in this run | WebKit and Chromium were used on macOS | Native Safari application and other OS/browser combinations not run | NOT TESTED — Outside verified environment |

## Fresh end-to-end evidence

`frontend/tests/mock-audit.spec.ts` uploads a new Thai customer CSV through the browser. Three records are accepted; one extra-field record is structurally rejected. Declaring `amount` as DOUBLE records the preserved `bad` value as a parse issue, so metadata reports three rows and two import issues. The required-customer check evaluates three rows and finds one failure. The frontend displays 33.3%; the downloaded CSV contains the failing `A,,bad` record and excludes the good Chiang Mai record. After the rule is renamed and the page is reloaded, its stored execution still shows the original rule name, counts, examples and download link.

The independent browser pipeline test imports six sales records and a two-row Thai region lookup. It casts with an explicit drop policy, deduplicates, joins, fills unmatched values, groups and sorts. Its actual totals are `เหนือ=350`, `ใต้=150`, `ไม่ทราบ=400`; CSV BOM bytes and rows are checked. The saved version also runs and produces a downloadable Parquet artifact. Separate backend tests decode exported Parquet and compare expected rows.

`backend/tests/test_mock_audit.py` contains the new backend regressions. Existing suites retain malformed-tail preservation, raw file identity, SQL restrictions, all quality kinds, each pipeline operation, quality-gate failure, cancellation, export cleanup and restart checks.

Injected API errors and delayed responses exist only in tests. Delayed-response tests obtain the actual backend response before withholding delivery; they do not invent query/profile/history data. Error injection verifies truthful error states, then settings retry uses the real endpoint.

## Saved data and local launch

The database was backed up before migration to `work/audit-20261013/metadata-before.sqlite3`. Before and after checks found 5 projects, 7 datasets, 6 rules, 1 standalone quality execution, 7 pipelines, 8 pipeline versions and 7 runs, with every table count unchanged. All 14 files under the saved workspace's `raw/` and `data/` retained the same SHA-256 hashes. Existing history without a rule snapshot is explicitly marked as lacking that metadata; no past rule definition is invented.

The local app is launched at [http://127.0.0.1:8000](http://127.0.0.1:8000) using `work/full-system-workspace`. Local logs and preservation evidence are under `work/audit-20261013/` and excluded from Git. No commit or push was performed.

To reproduce the browser checks, build the frontend, stop any service on port 8000, and run from `frontend/` with an unused temporary workspace:

```sh
DATAGUARD_WORKSPACE=/tmp/dataguard-audit-chromium npm run e2e -- --workers=1
DATAGUARD_WORKSPACE=/tmp/dataguard-audit-webkit npm run e2e -- --workers=1 --browser=webkit
```

Install the required Playwright browsers first if absent. This machine used the ignored `work/playwright-browsers` directory via `PLAYWRIGHT_BROWSERS_PATH`. See [remaining issues](REMAINING_ISSUES.md) for practical limits.
