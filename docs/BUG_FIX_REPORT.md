# Real-data verification: findings and fixes

Date: 15 October 2026. Existing uncommitted work was preserved; no commit, push, database reset, or migration was performed.

## 1. Duplicate occurrence count was unavailable

The existing uniqueness rule intentionally failed every row in each non-null duplicate-key group. A pair therefore reported 2 failures, whereas this acceptance fixture requires 1 occurrence beyond the first. Calling these the same metric would be misleading. The initial regression demonstrates that `count_mode=beyond_first` was silently ignored and an unknown mode was accepted.

An explicit optional count policy now supports both meanings. The UI labels both policies. Existing rules without this property retain all-group-row behavior, so saved pipeline semantics are preserved. The backend uses one failure relation for counts, examples, downloadable failures and pipeline gates. First occurrence follows stable source row ID; data without source row identity uses a stable column ordering. Null keys remain ignored. Invalid modes are rejected. No SQLite schema change.

Reproduction and proof: `unique-before-fix.log` (2 failures), `backend.log`, `test_real_data_contract.py`, and actual browser `quality-one-duplicate.png`: 6 evaluated, 1 failed, 16.7%. The all-group policy is separately verified as 2.

Files: `backend/app/quality.py`, `frontend/src/Quality.tsx`, `backend/tests/test_real_data_contract.py`, `docs/API.md`, `docs/UserGuide.md`.

## 2. Invalid-encoding upload reported an empty file structure

A decoder exception before the CSV header became a rejection event. Inspection discarded its reason when no header was obtained, returning only “CSV contains no header or records.” This hid the useful diagnosis.

Inspection now returns the selected encoding and original parser/decode reason when no header can be read. Failed staging still cleans up its own temporary upload. Malformed imports with a valid header continue to retain the original bytes and explicit rejection reasons; no recovery assumptions or silent row dropping were introduced.

Reproduction and proof: initial browser failure log, `encoding-before-fix.log`, backend regression, browser invalid-import scenario with actual HTTP400 containing the encoding/decode detail.

Files: `backend/app/imports.py`, `backend/tests/test_real_data_contract.py`.

## Test harness corrections

The first new browser run passed 25 scenarios and failed 2 new scenarios. One used an exact `getByLabel` lookup on the nested SQL label and lost the match after its text changed. It now uses the actual textbox accessible role/name. The other expected a different generic import error and exposed finding 2 above. Export expectations were tightened to include every lineage column and compare numeric values according to format, rather than assuming CSV encodes DOUBLE values as integer text. These are test corrections, not application success claims.

Final rerun results and any further findings are in E2E_TEST_RESULTS.md.

## Controlled-driver startup correction

An immediate back-to-back browser rerun stopped before WebKit execution because a closed TCP connection in TIME_WAIT prevented the preflight port bind. The driver now uses SO_REUSEADDR for its preflight check; an active listening server still raises address-in-use. It also removes a previous success marker before attempting a new run, preventing stale result.json from being mistaken for a completed new run. This was a harness reliability issue; no application/database change was needed. Both final engine results remain separately evidenced.

File: `scripts/audit/real_data_system.py`.
