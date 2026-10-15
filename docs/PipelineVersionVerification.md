# Pipeline versions and quality-status verification

Verified 2026-10-13 (Asia/Bangkok). All write/execution regression checks used temporary databases and imported test files. No test pipelines were added to the saved user workspace.

## Root causes

The existing user's project `<existing-project-id>` contains one pipeline ID, `<existing-pipeline-id>`, currently named `test_pipeline2`, and two preserved versions. The whole saved workspace contains eight pipelines across six projects. The project-scoped list query returns every pipeline in the selected project; it does not collapse duplicate names or limit results to one.

The previous frontend retained the selected ID when the name was edited. Save then used PUT for that ID, appending a version and renaming the pipeline. The initial creation action was also labeled Save version, which obscured the distinction. Backend creation already used a new ID and separate INSERT statements; no name-based upsert or deletion was found. Historical definitions existed in SQLite but the UI had no version selector and the GET endpoint always returned the latest definition. The database does not store previous pipeline names, so the exact name used when the original v1 was created cannot be reconstructed reliably.

The rule's red ERROR badge represented its configured severity. A PASSED execution with zero failing rows was valid: severity describes what a failure would mean, while status describes the actual calculation. The ambiguity was presentation. Actual API execution errors previously appeared only as notifications; they now have an explicit ERROR status without presenting a quality-failure count.

## Implemented behavior

- New pipeline assigns a fresh draft ID and clears the selected saved ID. Save pipeline persists that ID as an independent pipeline at v1. Unsaved drafts are not inserted until a valid save.
- Save version appends to the selected ID and preserves earlier definitions. Names remain pipeline-level labels; duplicate names are supported and IDs distinguish records.
- Saved version retrieves any previous definition. Historical versions can be previewed, run, duplicated, or restored by appending a new version.
- Run and duplicate requests explicitly include the viewed version. Saving uses an expected latest-version value; stale concurrent edits receive 409 while their local draft remains visible.
- Existing legacy API clients can omit draft ID, selected version, and expected version. Backend version allocation still uses a serialized SQLite write transaction. Constraint conflicts return 409 rather than exposing an unhandled database error.
- Saved-list refresh failures remain visible with a retry action.
- Severity badges are explicit and neutral. PASSED/FAILED are actual backend results; RUNNING reflects an in-flight request; ERROR reflects a request/execution error, with no fabricated count. Validation semantics are unchanged.

## Database changes and preservation

No schema change, migration, reset, pipeline deletion, or version deletion was made for this fix. The existing schema version remains 3. The API reuses the existing pipeline primary key and `(pipeline_id, version)` constraint.

Before restarting the saved workspace, a SQLite backup was created under `work/pipeline-issue-backup/metadata-before.sqlite3`. Full rows were compared before and after for projects, datasets, pipelines, pipeline_versions, quality_rules, and quality_runs. They are identical: 6 projects, 8 datasets, 8 pipelines, 10 pipeline versions, 7 rules and 2 quality executions. Both v1 and v2 of the user's existing pipeline remain accessible through the new API. Existing user pipelines were not executed or edited during regression testing.

## Actual results

| Check | Result | Evidence |
| --- | --- | --- |
| Five named pipelines visible with distinct IDs | PASS | Browser-created drafts and POST responses use five unique persisted IDs; all five catalog buttons remain visible |
| Saving v2 of pipeline_test_01 preserves the other four | PASS | Complete before/after API records compared; existing isolated sentinel also unchanged |
| Previous configurations accessible | PASS | v1/v2 GET responses and UI selector; v1 executed; restoration appends v3 without deleting v1/v2 |
| Duplicate saved version | PASS | New ID/v1 created from viewed v1; definition compared exactly |
| Browser refresh persistence | PASS | All five catalog entries remain; each can be selected and executed |
| Real backend process restart persistence | PASS | Old process stopped, new process started against the same isolated database; all pipeline/version rows compared exactly |
| All five execute after restart | PASS | Browser queues each viewed version; completed status/version and downloaded CSV values verified |
| Duplicate names / database constraints | PASS | Same-name records have independent IDs; duplicate ID is rejected with 409 without upsert |
| Concurrent saves | PASS | Synchronized backend writers yield one successful version and one stale 409; old version preserved; browser conflict keeps local draft |
| Severity Error + execution Passed | PASS | Real required check evaluated 3 rows, found 0 failures, returned severity=error/status=passed |
| Severity Warning + execution Failed | PASS | Real uniqueness check finds the two duplicate records |
| Running status | PASS | Actual backend response delayed in transport; in-flight UI displays RUNNING without results |
| Execution error distinct from failed validation | PASS | Real incompatible integer/string foreign-key fixture returns HTTP 400; UI displays ERROR and no failed-row metric; no false failed quality history is inserted |
| Browser/runtime errors | PASS | No unexpected page/console errors in the evidence workflow; expected HTTP 400 from the deliberate conversion-error fixture; no server traceback |
| Backend suite | PASS | 84 passed; one upstream Starlette/AnyIO deprecation warning |
| Frontend unit tests | PASS | 2 passed |
| Lint, TypeScript, production build | PASS | Completed successfully |
| Full Chromium suite | PASS | 24 passed, 1 restart-only case skipped in the ordinary invocation |
| Full Playwright WebKit suite | PASS | 24 passed, 1 restart-only case skipped in the ordinary invocation |
| Restart-only browser case | PASS | Executed separately by the controlled runner; 1 passed after real restart |
| Installed Safari / Firefox / other operating systems | NOT TESTED | WebKit and Chromium tested on macOS; no native Safari compatibility claim |

The controlled runner first executes two browser checks (five pipelines and quality statuses), performs a real restart, then executes the restart-only browser check. The ordinary suite skips that last case to avoid restarting an unmanaged server. The runner refuses to start if port 8000 is already occupied and verifies the server's reported workspace before any browser writes.

The first ordinary full-suite run had two observer timeouts because old tests matched run URLs without a version query parameter. Their request observers now match the pathname, while the new tests check the explicit version parameter. Both complete suites passed afterward. One browser launch was delayed by an automatic permission-review timeout; its retry completed successfully.

## Final isolated evidence

| Pipeline | Database ID | Final latest version | Verified output computed values before/after restart |
| --- | --- | --- | --- |
| pipeline_test_01 | 389330960f624f2d98901f826765ae1a | 3 (v1 restored as new v3; v2 retained) | 11, 21 |
| pipeline_test_02 | 05f5d732d470495b8aa0b40ca9ab9e72 | 1 | 12, 22 |
| pipeline_test_03 | 387c240db2f64f698e72515579590208 | 1 | 13, 23 |
| pipeline_test_04 | 26b4137e86d44549aee15b6a93d29e87 | 1 | 14, 24 |
| pipeline_test_05 | f07f6637bd654b63a269e02ded7b1575 | 1 | 15, 25 |

The v2 configuration of pipeline_test_01 computes 110 and 120; the independent backend regression executes and checks v2 directly. The browser also explicitly executes original v1 before restoration. The evidence directory contains screenshots, the ID/run mapping, complete pipeline/version row snapshots and server logs before/after restart.

## Exact files changed for this request

All paths below are relative to the repository root. Earlier audit changes in the working tree were preserved.

| File | Change |
| --- | --- |
| `backend/app/main.py` | Draft ID creation, selected-version retrieval/duplication/run, expected-version conflict, safe database constraint handling |
| `frontend/src/api.ts` | Typed selected_version response |
| `frontend/src/PipelineBuilder.tsx` | Independent draft IDs and save modes, full catalog, historical selector, restore/duplicate/run controls, persistent save/list errors |
| `frontend/src/Quality.tsx` | Explicit severity and separate running/result/error states |
| `frontend/src/Runs.tsx` | Distinct quality execution-status and rule-severity column labels |
| `frontend/src/style.css` | Version toolbar spacing and neutral severity badges |
| `frontend/playwright.config.ts` | Explicit external-server mode for the controlled restart runner |
| `frontend/tests/independent-audit.spec.ts` | New-save label and version-aware run request observers |
| `frontend/tests/mock-audit.spec.ts` | New-save empty-state label |
| `backend/tests/test_pipeline_versions.py` | Five IDs/versions/executions, duplicate names/constraints, concurrency and severity/error regressions |
| `frontend/tests/pipeline-versions.spec.ts` | Five-pipeline UI/refresh/run workflow, historical controls, quality statuses, stale draft and actual-restart checks |
| `scripts/audit/pipeline_persistence.py` | Isolated server/process restart orchestration and SQLite row comparison |
| `docs/API.md` | Additive version/draft/conflict API documentation |
| `docs/UserGuide.md` | Pipeline identity/version and severity/status instructions |
| `docs/PipelineVersionVerification.md` | This report |
| `README.md` | Verification/reproduction link |

## Reproduction and limitations

Stop the normal app to free port 8000, build the frontend, then run:

```sh
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright-browsers" .venv/bin/python scripts/audit/pipeline_persistence.py
```

Tests use an automatically created temporary workspace and do not create pipeline_test_* records in user projects. Browser dependencies must be installed. The runner leaves its test workspace/evidence for inspection and stops its server afterward. Restart the normal app with the desired saved workspace.

Unsaved drafts require Save pipeline to survive reload. Pipeline names are not versioned metadata. Historical versions can be inspected, executed, duplicated, and restored; this change provides no destructive version-delete action. Quality execution errors are explicit transient UI states and logged backend responses, not completed quality-history records; passing/failing calculations remain persisted. Native Safari was not automated. No commit or push was made.

The normal application is running at [http://127.0.0.1:8000](http://127.0.0.1:8000) with the original saved workspace.
