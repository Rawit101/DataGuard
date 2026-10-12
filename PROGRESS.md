# DataGuard progress

## Scope and decisions

- Build a local single-URL React/FastAPI app with managed raw uploads, DuckDB dataset snapshots, SQLite metadata, quality checks, SQL, pipelines, runs, and exports.
- Keep raw uploads immutable. CSV structural rejects remain in the managed raw file and are recorded with line, content, and reason. Typed parse failures remain visible as raw values and are never silently discarded.
- User SQL uses only SELECT/CTE queries against approved dataset aliases. It runs in a distinct DuckDB connection with file access limited to the project's managed snapshots. Trusted imports, pipelines, and exports use separate connections.
- Serialize active processing jobs. Persist queued and running states in SQLite, run work in a subprocess, and repair interrupted state on restart.
- Preview and full runs compile from the same pipeline definition. Previews execute the complete relation before applying an output row limit.

## Current status

The integrated MVP, demo, tests, and documentation are implemented. Backend tests (12 passed), frontend tests (2 passed), type check/build, lint, clean `npm ci`, and dependency audit (zero advisories) pass. A browser workflow imported both CSV fixtures into a new project, inspected data and parse issues, ran and saved SQL, checked quality, previewed and ran a cleaning/join/aggregation pipeline, and produced matching five-row CSV and Parquet outputs. A server restart confirmed the runs and artifacts persisted. The profile display and completed-job polling defects found during this run were fixed and rechecked. See [Verification](docs/Verification.md) for the detailed results. Playwright Chromium could not launch inside the available macOS sandbox; its browser test file remains ready for an unrestricted local run.
