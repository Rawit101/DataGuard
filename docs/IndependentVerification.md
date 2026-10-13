# Independent verification and fixes

This review cloned `https://github.com/Rawit101/DataGuard` at commit
`6aa6111474a0a61926af86b4f4f3d87711fddc22` and ran the application and tests
independently on 12 October 2026. The fixes in this working copy have not been
pushed to the remote repository.

Environment: Linux, Python 3.12.14, Node 24.19.0, DuckDB 1.4.2, Playwright
1.56.1 with Chromium 141. Tests used isolated projects and workspaces, not the
user's running Mac server or their saved datasets.

## Findings corrected

| Problem reproduced | Change | Regression evidence |
| --- | --- | --- |
| Switching to a dataset with a different schema retained the previous filter, produced an unknown-column request, and could leave old rows displayed | Reset dataset-specific controls and loaded results; ignore superseded row requests and show loading/error states | Browser dataset-switch regression |
| Running a different quality rule from the list changed the selected ID but left the old editor configuration visible; Save could overwrite the wrong rule | Populate the editor from the rule actually executed and clear the previous result while running | Browser checks the editor fields and persisted rule after Save |
| The SQL editor's initial dataset response could replace or interleave with a newly typed query | Wait for initial dataset loading before editing; preserve a nonempty draft and quote the default alias | Browser full-result export and SQL-edit tests |
| A delayed SQL response could display results for an earlier query after the user edited the SQL, while Export would use the new SQL | Track query revisions and discard obsolete responses | Browser holds a real API response, changes the query, releases the response, and checks that obsolete results are not displayed |
| Editing a pipeline's output format while its saved definition was loading could be overwritten by the late response | Disable pipeline controls during loading, saving, and preview execution | Fresh browser pipeline changes to CSV/BOM after reloading and verifies the actual output |
| A numeric string larger than signed BIGINT was accepted without an import issue | Validate the signed 64-bit range while preserving the original string | Backend overflow regression |
| Valid NaN/Infinity values made SQL results or Parquet inspection/profile fail during JSON serialization | Recursively serialize special numeric values as explicit strings for display, without modifying stored/exported data | Backend SQL and Parquet regressions |
| A user column named `__rank` collided with the temporary deduplication rank and could cause all rows to disappear | Choose an internal rank alias that does not collide, including case variants | Backend asserts exact retained rows and original user values |
| A user column named `__duplicate_count` collided with the unique-rule window count | Choose a collision-free helper name and remove only that helper | Backend asserts duplicate count, examples, and exported user column |
| Large CSV commit performed a separate DuckDB insert for each record and did not finish within a 180-second request timeout on the 1-million-row fixture | Stream validated accepted rows to a temporary canonical CSV, then bulk COPY to Parquet using explicit schema/options | Full backend suite, multiline/null/raw-value regressions, and independent 1-million-row load |

Browser tests now use `work/playwright-workspace` by default, rather than
adding verification projects to the normal user workspace. An explicit
`DATAGUARD_WORKSPACE` still takes precedence.

The canonical CSV uses Python 3.12's `QUOTE_NOTNULL` and DuckDB's
`allow_quoted_nulls=false` so NULL and literal empty text remain distinct.
Malformed-record decisions and source row IDs still come from the existing
parser. Original source bytes remain unchanged. The temporary accepted-record
file is removed on success or failure. No new runtime dependency is required.

## Checks completed on the corrected code

| Check | Result |
| --- | --- |
| Backend suite | 55 passed: the original 47 plus 8 independent checks |
| Frontend unit tests | 2 passed |
| ESLint | Passed |
| TypeScript and production build | Passed |
| Playwright Chromium end-to-end suite | 11 passed: the original 2 plus 9 independent browser scenarios |
| Large CSV / real worker / actual server restart | Passed; measurements below |
| Whitespace/error check on changes | `git diff --check` passed |

The original backend suite was run before changes and passed all 47 tests.
The original two Chromium browser tests also passed. New checks then reproduced
the failures described above; corrected tests were rerun after the changes.

## Browser evidence

The browser suite exercised actual server responses and processing, including:

- CSV import and preview through the file chooser, with Thai text.
- Discarding an import preview without committing a dataset.
- Dataset selection with incompatible prior filters.
- Quality-rule editing and all eight rule configuration forms. Expected failed
  counts on the new fixture: required 1, unique 2, parse-valid 1, range 2,
  accepted 1, regex 0, referential integrity 1, row-count bounds 1.
- Native download completion for the quality-failure CSV, full SQL-result CSV
  with BOM, runnable SQL file, and pipeline Parquet output.
- A full 120-row query export, verified by reading the downloaded file and
  checking all rows including the final Thai value, rather than only 50 preview
  rows. Saved-query deletion was checked against persisted API state.
- A fresh non-demo six-row Thai sales fixture. Through the pipeline UI, cast
  invalid amounts with an explicit drop policy, deduplicate, left join a lookup,
  fill unmatched names, aggregate, and sort. Expected totals were calculated
  directly from the fixture: North 350, South 150, Unknown 400. The browser
  saved and ran Parquet and CSV/BOM versions, downloaded the output, and checked
  the CSV values and aggregate source counts.
- A real export and pipeline job with a 10,000-by-10,000 join (100 million
  potential output rows), cancelled using browser controls after the backend
  reported running. Both reached cancelled status; the project had no published
  artifacts. These are cancellation checks, not successful 100-million-row
  throughput benchmarks.
- A delayed SQL response test uses a real server response with delayed delivery;
  it does not substitute mock query data.

API-assisted fixture creation is used in several browser scenarios to isolate
the control under test. It is not claimed that every setup step was manually
performed in the browser. The original file-chooser scenario verifies UI import.

## CSV load and persistence measurements

`scripts/audit/independent_load.py` generated a deterministic CSV, calculated
expected group counts and sums in Python while creating the fixture, imported
it over HTTP, queried it, ran a saved pipeline through the real worker, read
back the downloaded Parquet, stopped and restarted the server, and verified
that the dataset, completed run, and artifact bytes persisted.

| Measurement | Observed result |
| --- | --- |
| Input | 1,000,000 rows, 107,670,810 bytes (about 108 MB) |
| Inspection/upload request | 1.601 seconds |
| CSV commit after bulk-load fix | 5.536 seconds |
| Last 50-row page request | 0.437 seconds |
| Grouped SQL query | 0.041 seconds |
| Queued-to-completed pipeline | 0.311 seconds |
| Exported summary | 10 rows, 808-byte Parquet; exact counts and sums matched |
| Real server restart | Dataset, completed run, and identical artifact download retained |
| API/worker memory | Unavailable: this runtime did not expose usable process RSS measurements |

Before bulk loading, the same CSV commit did not finish within the driver's
180-second request timeout. That is a lower-bound observation, not a measured
completion time. These are single local-run timings; they are not general
performance guarantees or measurements of peak memory. The driver creates a
temporary workspace and cleans it up.

## Reproduce

Install dependencies using the README, then run from the project root:

```sh
.venv/bin/python -m pytest -q backend/tests
cd frontend
npm ci
npx playwright install chromium
npm run test
npm run lint
npm run build
npm run e2e -- --workers=1
cd ..
.venv/bin/python scripts/audit/independent_load.py
```

For a smaller load:

```sh
DATAGUARD_AUDIT_ROWS=10000 .venv/bin/python scripts/audit/independent_load.py
```

The load driver uses port 8001 and a fresh temporary workspace. Browser tests
start their own app on port 8000; stop an existing service there before running
them. To launch the actual app, use `.venv/bin/python scripts/dev.py`.

## Remaining scope and limitations

- The original independent run used Linux. The macOS integration rerun is
  recorded below; Windows remains unverified. The browser-test server command
  still uses the POSIX virtual-environment path.
- No claim is made that every individual visual control was exhaustively
  tested. The eleven browser scenarios and backend suite define the verified
  coverage. Schedulers, database connectors, PySpark execution, a graphical
  pipeline canvas, and editable Settings remain future features.
- Pipeline steps still use JSON editing and Settings still displays configuration
  read-only. Both are real current behaviors, not mock responses.
- CSV import is still a synchronous request, without background import progress
  or an import Cancel button. The bulk loader improves the tested workload;
  wider formats and larger files still need bounded testing.
- Import needs temporary disk space for the accepted-record CSV in addition to
  raw data and the output. Discard preview removes the UI preview; automatic
  expiry of uncommitted staging files is not implemented by these changes.
- Backend tests report one Starlette/AnyIO deprecation warning. No dependency
  upgrade was performed solely to suppress that warning.
- This review did not find an active path substituting mock data for real
  processing. The documented sample project is explicitly labeled and isolated.
  This finding is scoped to inspected code and executed scenarios.

## Integration rerun in the existing macOS project

On 12 October 2026, the attached patch was checked with `git apply --check`
and applied cleanly to the `clean-source` checkout. The checkout had no local
changes before integration. The project used Python 3.12.14, Node 22.23.2,
npm 10.9.8, DuckDB 1.4.2, and the existing pinned dependencies. The Python
environment passed `pip check`; the patch changed no dependency manifests.
All application and browser tests used isolated temporary workspaces. The
saved `work/full-system-workspace` data was not used for these tests.

| Local check | Actual result |
| --- | --- |
| Backend suite | 55 passed; one Starlette/AnyIO deprecation warning |
| Frontend unit tests | 2 passed |
| ESLint | Passed |
| TypeScript and production build | Passed |
| Playwright Chromium end-to-end suite | 11 passed in 15.3 seconds with one worker |
| One-million-row CSV, SQL, pipeline, export, and server restart | Passed; exact group totals and artifact bytes survived restart |
| `git diff --check` | Passed |

The macOS load driver generated 1,000,000 CSV rows (107,670,810 bytes).
Inspection took 0.780 seconds, CSV commit 2.535 seconds, the last page 0.179
seconds, grouped SQL 0.019 seconds, and the pipeline 0.319 seconds. The
downloaded Parquet contained 10 rows and 808 bytes. These are one-run local
measurements, not performance guarantees.

**Blocked checks:** the first Playwright attempt could not launch Chromium in
the restricted sandbox (`MachPortRendezvousServer` permission denied), so all
11 scenarios stopped before reaching DataGuard. The same suite passed after
running Chromium with the required macOS process permission. No requested
functional check remains blocked. Process peak RSS was not measured: the
driver reads Linux `/proc`, which macOS does not provide.

The fixes remain uncommitted and unpushed in this existing project. Local
datasets, installed dependencies, browser downloads, and generated builds
remain outside Git tracking.
