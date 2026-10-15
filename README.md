# DataGuard

DataGuard is a local data engineering workspace. One browser app lets you import CSV or Parquet, inspect and query approved datasets, check quality, build repeatable DuckDB pipelines, and download CSV or Parquet results. It uses React, TypeScript, Vite, Tailwind, FastAPI, DuckDB, and SQLite. No cloud account or Spark installation is required.

## Install and launch

Use Python 3.12 and Node.js 20.19+ or 22.12+; this build was verified with Python 3.12 and Node 22 on macOS. From this directory:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
python scripts/dev.py
```

Open the single printed URL, `http://127.0.0.1:8000`. The launcher builds the UI, starts the API and worker, checks the port, and stops the services on exit. On Windows, use `py -3.12 -m venv .venv`, `.venv\Scripts\activate`, and `py scripts/dev.py` in a terminal; the rest of the commands are the same. Windows has not been exercised in this environment.

For a local built app on one port:

```sh
cd frontend
npm run build
cd ..
python scripts/start.py
```

Open `http://127.0.0.1:8000`. Keep the terminal open while using the app. Data is saved under `.dataguard/` by default and remains there across restarts. To choose another location, set `DATAGUARD_WORKSPACE` to a writable directory before launch. See [.env.example](.env.example) for the other optional settings; it is a reference file, not automatically loaded.

On this machine, the saved audit projects moved with this folder are under `work/`. This directory contains local data and browser downloads and is excluded from Git. To reopen those existing projects from this directory on macOS, launch with:

```sh
DATAGUARD_WORKSPACE="$PWD/work/full-system-workspace" .venv/bin/python scripts/start.py
```

Other saved verification workspaces are also under `work/`. Set `DATAGUARD_WORKSPACE` to the workspace you want before launching; each workspace has its own projects and files. A fresh clone starts with an empty workspace; use **Load sample project** to populate it.

## First run

Choose **Load sample project** on Overview, then follow [the user guide](docs/UserGuide.md). The built-in synthetic data has deliberate errors. The sample pipeline yields five province groups; Bangkok has two listings and an average of 75,000 THB per square metre. The sample action creates a new project and does not alter existing projects.

## Checks

```sh
.venv/bin/python -m pytest -q backend/tests
cd frontend
npm run test
npm run lint
npm run build
npm run e2e
```

Browser tests use Playwright Chromium. Install its browser once with `npx playwright install chromium`. Backend integration tests use isolated temporary workspaces. The API docs are available at `/docs` while the app is running.

## Troubleshooting

- **Port in use:** close the other service on port 8000, then rerun the launcher.
- **Python package error:** activate `.venv` and reinstall `backend/requirements.txt`.
- **npm cache permissions:** use a writable cache, for example `npm_config_cache=./.npm-cache npm ci` from `frontend/`.
- **No frontend at port 8000:** build it with `npm run build` first.
- **Unsupported file or encoding:** CSV supports UTF-8, UTF-8 BOM, UTF-16, and Latin-1; Parquet support is built in. Use Import issues to inspect rejected CSV records.
- **Large file or slow query:** adjust the upload, DuckDB memory, or SQL timeout settings before launch. Resource limits are safeguards for normal local use, not a security sandbox for hostile local users.

See [Architecture](docs/Architecture.md), [API](docs/API.md), [Development](docs/Development.md), [Roadmap](docs/Roadmap.md), and the [end-to-end verification report](docs/Verification.md). The app binds to loopback and makes no external dataset uploads. It is intended for one local user and one app process.

The [full system verification](docs/full-system-verification.md) records the current audit, fixes, browser workflow, load check, and environment limits. The [functionality audit](docs/functionality-audit.md) and [fresh-data verification](docs/real-functionality-verification.md) provide earlier trace details. To try the same fixtures, create a new project and import the files under `backend/tests/fixtures/`; the original demo and your existing projects are separate. The fresh-data pipeline's expected totals are `เหนือ=350`, `ใต้=150`, and `ไม่ทราบ=400` after cleaning, joining, and grouping.

See the [independent verification and fixes](docs/IndependentVerification.md) for the subsequent Linux audit, additional browser regressions, and the 1-million-row CSV check. Browser tests now default to an isolated `work/playwright-workspace` directory, or use the workspace explicitly set through `DATAGUARD_WORKSPACE`.

The reproducible load and restart check is `.venv/bin/python scripts/audit/independent_load.py`. It starts its own server on loopback port 8001 and uses a temporary workspace. Set `DATAGUARD_AUDIT_ROWS=10000` for a smaller check. CSV imports require Python 3.12+ and temporary disk space for a normalized accepted-record file; raw source bytes remain unchanged. Nonfinite numeric values appear as the strings `NaN`, `Infinity`, and `-Infinity` in JSON previews; exported files retain their underlying values.

See the [2026-10-13 mock-data audit](docs/MOCK_DATA_AUDIT.md), [functional verification](docs/FUNCTIONAL_VERIFICATION.md), and [remaining issues](docs/REMAINING_ISSUES.md) for the latest repository-wide audit and actual test results. Metadata schema 3 preserves execution-time quality-rule snapshots with a forward migration that retains existing history.

The [pipeline/version and quality-status verification](docs/PipelineVersionVerification.md) documents the independent-pipeline controls, historical versions, severity labels, and the five-pipeline browser test across a real backend restart. Reproduce it with `.venv/bin/python scripts/audit/pipeline_persistence.py` after stopping the app on port 8000.

## Real-data acceptance verification

The October 2026 audit uses physical deterministic CSV fixtures and real browser/API/worker/database processing. Start with [the test plan](docs/E2E_TEST_PLAN.md), then see [actual results and reproduction commands](docs/E2E_TEST_RESULTS.md), [the feature matrix](docs/FEATURE_VERIFICATION_MATRIX.md), and [fixes](docs/BUG_FIX_REPORT.md). The controlled driver refuses an occupied port and uses fresh temporary storage; stop your normal app before running it and restore its original workspace afterward. It does not modify your saved workspace.
