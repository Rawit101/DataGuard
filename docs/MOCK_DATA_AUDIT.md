# Repository mock-data and functionality audit

Audit date: 2026-10-13 (Asia/Bangkok). Audited starting commit: `d201cdf`. The working tree was clean at the start. Changes in this audit are uncommitted.

## Conclusion and scope

No active user-facing path was found that substitutes invented records, dashboard values, query results, or success responses for backend work. The audit covered all seven pages, shared frontend components and API helpers, all 44 route handlers including static frontend serving, every application backend module, launch/build scripts, dependency/configuration files, tests, fixtures, and project documentation. Generated dependencies, browser binaries, builds, and private workspaces were excluded from source review.

The review did find real correctness and error-handling defects. These were reproduced with regression checks or traced through their write paths, then fixed. The initial new backend regression run had 11 failures; the final combined backend suite has 80 passing cases. Final browser suites have 21 passing tests each in Chromium and Playwright WebKit.

## Source inventory and mock classifications

| Area | Source and data path | Finding |
| --- | --- | --- |
| App/navigation/API | `frontend/src/App.tsx`, `main.tsx`, `api.ts:11`, `Components.tsx` | Real fetch calls; non-2xx responses throw. Empty arrays/null initial state and defensive display defaults do not generate records. |
| Overview | `frontend/src/Overview.tsx:21`, `backend/app/main.py:189` | Counts and lists come from SQLite projects, datasets, pipelines, and the latest ten runs. No invented KPI values. |
| Datasets | `frontend/src/Datasets.tsx:36`, `backend/app/imports.py:214`, `engine.py:118` | Real multipart uploads, immutable raw copies, Parquet snapshots, SQLite metadata/issues, and DuckDB paging/filtering/sorting. |
| SQL Workspace | `frontend/src/SqlWorkspace.tsx:30`, `backend/app/engine.py:61` | Guarded read-only SQL against imported project aliases; actual rows, full counts, durations, saved SQL, and queued exports. |
| Data Quality | `frontend/src/Quality.tsx:33`, `backend/app/quality.py:116` | Eight rule kinds evaluate actual datasets. Counts, bounded examples, complete failure CSVs, and history are persisted. |
| Pipeline Builder | `frontend/src/PipelineBuilder.tsx:47`, `backend/app/pipeline.py:137`, `main.py:415` | JSON templates are editable defaults. Real compiled DuckDB plans execute; versions persist in SQLite. |
| Runs & Exports | `frontend/src/Runs.tsx:18`, `backend/app/worker.py:51` and `:152` | Persisted queue/status/cancellation; real step counts, quality results, lineage and files. Timers poll actual jobs. |
| Settings & Help | `frontend/src/Help.tsx:10`, `backend/app/main.py:153` | Read-only runtime configuration. Schema version now reads metadata rather than a fixed response value. Help text describes the sample; it is not a live metric. |
| Guided sample | `backend/app/main.py:582`, `demo/*.csv`, Overview's explicit sample button | Intentionally synthetic tutorial input, imported as a new separate project through the real ingestion/rule/pipeline paths. No automatic demo fallback. Retained. |
| Storage/runtime | `backend/app/db.py`, `config.py`, `serialization.py`, `worker.py` | Real SQLite and isolated DuckDB connections. Serialization of dates, decimals and nonfinite values is display conversion, not fabricated data. |
| Scripts/configuration | `scripts/`, `frontend/scripts/`, manifests, `.env.example`, `.gitignore` | Launch/build/test routines and real audit fixtures. Sleep/delay calls wait for processes or jobs. No external service credential is required. `.env.example` is the only tracked environment file. |
| Test fixtures/mocks | `backend/tests/`, `frontend/tests/`, `frontend/src/api.test.ts` | Fetch stubs, deliberately delayed responses and injected API errors are test-only controls. They are absent from runtime data paths. |
| Accounts/connectors/charts | Application source, API route inventory and `docs/Roadmap.md` | No implemented account/user-management system, external database connectors, scheduled alerts, or chart/global-score subsystem. No fake integrations or substitute chart data were found. |

## Findings and applied fixes

References below point to the current fix locations. P1 means a persisted operation or saved workflow could be corrupted or made unusable; P2 means inaccurate display, validation, or error handling; P3 means a smaller usability/error-reporting problem.

| Severity | Original finding | Fix location and action | Evidence |
| --- | --- | --- | --- |
| P1 | Invalid output formats/paths and malformed pipeline objects could return a saved-success response. Duplicate column names differing by case could make the declared plan disagree with DuckDB output. | `backend/app/pipeline.py:79` validates output/step shapes and names; `main.py:128` binds the plan before saving. | `test_invalid_pipeline_is_rejected_before_saving`, including nested malformed expressions/aggregations and case collisions. |
| P1 | Concurrent saves calculated the next version outside the write transaction. | `backend/app/main.py:440` allocates/inserts versions under `BEGIN IMMEDIATE`. | Two synchronized API saves produce versions 2 and 3 without losing either. |
| P1 | Deleting a quality rule could break saved pipeline versions referencing that rule. | `backend/app/main.py:379` rejects deletion while any saved version references it. | Referenced-rule deletion returns 400 and preserves the rule/pipeline. |
| P2 | Malformed rule type settings could cause an unhandled server error; Python-valid regex syntax could be unsupported by DuckDB; invalid/reversed range bounds could be saved. | `backend/app/quality.py:17` checks configuration shapes; `:86` validates bounds and regex syntax using the execution engine. | Bad type, range and RE2-pattern regressions return 400 without creating records. |
| P2 | Parquet INTEGER/parameterized DECIMAL columns lacked numeric means. Explicit Parquet overrides did not create parse issues. | `backend/app/engine.py:158` recognizes native numeric types; `imports.py:283` records failed override casts while preserving values. | INTEGER/DECIMAL means are 1.5; `bad` remains stored and is recorded as one issue. |
| P2 | CSV parse issues used logical record numbers as physical lines after multiline records. Unparameterized DECIMAL overflow was accepted by the import validator. | `backend/app/imports.py:76` checks DECIMAL(18,3) bounds; `:104` tracks physical record start lines. | Multiline fixture reports line 4; decimal overflow stays in raw data and appears as an issue. |
| P2 | Historical executions had no rule snapshot. A changed rule could not reliably label a previous result. | `backend/app/db.py:99` adds a nullable snapshot column and migrates schema 1/2 to 3; `quality.py:167` persists snapshot details; `main.py:399` exposes decoded history. | Rule edit/reload and old-schema migration tests; legacy entries remain present with an explicitly unavailable snapshot. |
| P2 | Late profile, import-issue, quality-history or run-detail responses could replace a newly selected item's state. | `frontend/src/Datasets.tsx:38`, `Quality.tsx:35`, `Runs.tsx:22` guard response ownership and clear previous views. | Delayed real profile/history/run-detail browser regressions pass. Issue-request guard is also inspected in source. |
| P2 | API failures could leave indefinite loading text or misleading empty-state messages on pages. | Loading/error/retry states in `Overview.tsx:18`, `Datasets.tsx:12`, `Help.tsx:8`, `Quality.tsx:25`, `Runs.tsx:14`, `SqlWorkspace.tsx:11`, `PipelineBuilder.tsx:42`. | Injected API-failure checks across all seven pages; settings Retry then reads real runtime configuration. |
| P2 | A failed row-count bound on an empty dataset displayed a 0% row-failure rate. | `frontend/src/Quality.tsx:52` distinguishes failed dataset checks from individual failed rows and shows no invented percentage. | Empty dataset with minimum 1 displays a failed check; stored evaluated rows remain 0. |
| P2 | WebKit native selects let long options enlarge the mobile document and ignored the intended control height. | `frontend/src/style.css:91` explicitly styles/contains the select layout while preserving native selection. | Failed 375-px WebKit case was reproduced; corrected document is 375 px; final viewport and native type-ahead checks pass in both engines. |
| P3 | Native Parquet type options could appear missing or send an unsupported explicit override when restored. Import file controls could continue showing a previously committed file. | `frontend/src/Datasets.tsx:41` resets the input; `:46` exposes the inferred type and removes an override when the original type is selected. | Native Parquet API regression and frontend build/source inspection; successful import/control reset covered by browser upload. |
| P3 | Pipeline SQL download network errors could escape its event handler. | `frontend/src/PipelineBuilder.tsx:62` reports errors through the normal notification path. | Real runnable SQL download passes; error handling inspected; no unhandled browser errors in audit flows. |

## Recheck

The final search for mock/fake/dummy/simulated/TODO/FIXME/NotImplemented markers in application source and launcher scripts found no matches. Broader terms were classified above: HTML placeholders are field hints, UUIDs identify actual records, sample constants are tutorial expectations, and timers are polling/notification lifecycles. No production data fallback was introduced by these fixes. See [functional verification](FUNCTIONAL_VERIFICATION.md) for actual test results and [remaining issues](REMAINING_ISSUES.md) for scope limits.
