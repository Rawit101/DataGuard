# DataGuard final pre-commit review

Reviewed 15 October 2026 (Asia/Bangkok), against HEAD `d201cdf5f599583d87569b3bda9b37355b83af40`.

## Decision

**Ready to commit the explicit 55-file manifest below, subject to your approval.** No blocking application regression was found in the reviewed changes or executed regressions. Nothing is staged. No commit, push, reset, checkout, history rewrite, or remote update was performed. Git inspection commands were read-only.

The complete tracked-file inventory and all modified/untracked files were inspected. Whole-repository file-size discovery inspected over21,000 files, including ignored dependencies/runtime storage. Credential-pattern scanning covered all72 tracked files and32 untracked files at the start (104 files), with file-type/size/provenance checks. The review's new report is included in the final rescan.

## Findings resolved or explicitly excluded

1. **Ignore-rule gaps resolved.** Existing rules protected normal workspaces, dependencies, browsers, .env files and build outputs, but did not protect a SQLite/DuckDB database or credentials file placed elsewhere in the repository. `.gitignore` now also excludes database files/journals, credential stores/key files, root managed raw/data/uploads/exports/tmp/artifacts directories, nested .dataguard directories and common additional caches. The safe `.env.example` and curated fixtures remain includable. No tracked source file became ignored; Git's tracked-but-ignored query is empty.
2. **Two duplicate root reports excluded.** `E2E_TEST_RESULTS.md` and `FEATURE_VERIFICATION_MATRIX.md` at repository root are preserved as local untracked files. They are delivery copies whose evidence links do not belong in the repository root. Only their canonical docs/ counterparts are in the proposed manifest. Avoid a blanket add-all operation.
3. **Source documentation made portable/private.** The canonical E2E results/matrix now describe ignored logs as local generated evidence paths rather than repository links to files that will not exist in a fresh clone. Reproduction commands create that evidence. Personal absolute repository paths and production project/pipeline IDs were removed from the relevant canonical reports. Fixture IDs and dated historical test counts remain appropriately identified as test/historical evidence.
4. **No unwanted content in the proposed commit.** No credential signatures, private keys, credential-bearing URLs, local database magic, executable/browser binaries, symlinks, uploaded user files, generated exports, screenshots, execution logs, backups or temporary test artifacts occur in the manifest. Curated deterministic CSV/JSON fixtures are explicitly included; all12 match regeneration in a separate temporary directory byte for byte. The intentionally malformed, empty and invalid-encoding CSVs are regression inputs.
5. **File limits verified.** Largest candidate is tests/fixtures/e2e/large_dataset.csv at1,783,303 bytes (~1.70 MiB). All11 local files >=10 MiB are ignored and untracked; browser binaries/dependencies remain local. No proposed file exceeds100,000,000 bytes. All88 blobs reachable from current HEAD are also below that conservative threshold; largest existing blob is package-lock.json at145,283 bytes. Unreachable objects and remote state were not altered or exhaustively secret-scanned.

Credential scanning used multiple key/private-key/provider-token patterns plus literal credential and credential-bearing URL checks, with manual configuration review. No matches were found. This is a scoped review, not a guarantee that every conceivable secret format can be recognized. `.gitignore` does not remove previously tracked files, so the separate tracked-but-ignored and candidate-content checks were necessary.

## Code review coverage

- Independent draft IDs, POST versus version PUT, explicit viewed versions, immutable history, duplicate IDs/names, and transactionally allocated versions with stale-save409 handling.
- SQLite schema2→3 forward migration adds a nullable quality-rule snapshot column and preserves old records. This migration is part of the overall pending change set even though the latest issue fix itself needed no migration. Legacy history remains readable and is explicitly labeled when snapshot metadata is absent.
- CSV physical line locations, malformed-tail preservation, parse/DECIMAL issue reporting, Parquet type overrides and typed profiling.
- Pipeline output/step validation, column collisions and binding before save; strict quality gates and late-publication cleanup regression.
- Explicit severity versus execution status, existing all-group versus optional beyond-first duplicate policies, persisted quality snapshots and referenced-rule deletion protection.
- Response-ownership/loading locks and visible errors across datasets, SQL, quality and run details; version-aware browser request observers; WebKit select sizing and keyboard/viewport checks.
- New tests run real processing and compare actual values. Two earlier error-display tests deliberately inject500; delayed-response cases deliver actual backend data. No production fake-success path was introduced.

No application code was changed during this pre-commit review. Review-time changes are limited to .gitignore hardening, canonical report cleanup, and this review document. Existing application/source work was preserved.

## Fresh checks

| Check | Result |
|---|---|
| Backend suite | PASS:89 passed; one existing Starlette/AnyIO deprecation warning |
| Frontend unit tests | PASS:2 passed |
| ESLint | PASS: exit0, no findings |
| TypeScript and production build | PASS: exit0 |
| Chromium regular browser suite | PASS:27 passed;2 restart cases deferred to controlled phase |
| Chromium actual-restart phase | PASS:2 passed |
| WebKit regular browser suite | PASS:27 passed;2 restart cases deferred to controlled phase |
| WebKit actual-restart phase | PASS:2 passed |
| Full metadata/files across both isolated restarts | PASS: identical pre/post rows and hashes; exports remain byte-identical |
| Original saved workspace | PASS: all13 tables and all25 stored-file hashes identical to review start and previous audit baseline |
| Candidate secret/type/size and fixture provenance checks | PASS |
| Ignore-rule probes and tracked-file check | PASS |
| git diff --check | PASS |

**149 automated test executions passed;0 failed.** The ordinary browser invocations defer two cases each, and each controlled restart phase actually executes those cases; no required restart check was left skipped.

For this review, a disposable adapter under ignored work/precommit-review/ runs the existing controlled driver on port5173 with a temporary Playwright configuration. It changes only test transport/startup/configuration, uses the same actual source/build/tests, verifies the fresh workspace before writes, performs real process restarts and stops only its own server. Port5173 is already allowed by DataGuard's development Origin policy. The normal application on8000 remained running throughout; no origin/access guard was relaxed. The adapter and reports/logs are local artifacts and excluded from this commit.

Reproduce the standard suites with the documented setup and scripts/audit/real_data_system.py commands in docs/E2E_TEST_RESULTS.md. Stop the normal app first if using the default port8000 driver. This review's actual command/output evidence is stored locally under work/precommit-review/: backend.log, frontend-unit.log, lint.log, build.log, chromium/browser-before-restart.log, chromium/browser-after-restart.log, webkit/browser-before-restart.log, webkit/browser-after-restart.log, each engine's result.json and user-preservation.json. The temporary adapter/config are retained there for reproduction.

Original saved records remain6 projects,8 datasets,8 pipelines,10 versions,8 pipeline runs,7 rules,2 quality runs,9 query runs,2 export jobs and9 artifacts. There are no new regression projects in the user workspace. The original app remains at http://127.0.0.1:8000.

## Exact proposed commit manifest

Paths are relative to the repository root. Include only these55 changed/new files; unchanged existing source/configuration/lockfiles are already tracked and need no separate staging. Local generated evidence and the two duplicate root reports are excluded.

### Application and configuration (17)

- `.gitignore`
- `backend/app/db.py`
- `backend/app/engine.py`
- `backend/app/imports.py`
- `backend/app/main.py`
- `backend/app/pipeline.py`
- `backend/app/quality.py`
- `frontend/playwright.config.ts`
- `frontend/src/Datasets.tsx`
- `frontend/src/Help.tsx`
- `frontend/src/Overview.tsx`
- `frontend/src/PipelineBuilder.tsx`
- `frontend/src/Quality.tsx`
- `frontend/src/Runs.tsx`
- `frontend/src/SqlWorkspace.tsx`
- `frontend/src/api.ts`
- `frontend/src/style.css`

### Automated tests (9)

- `backend/tests/test_mock_audit.py`
- `backend/tests/test_pipeline_versions.py`
- `backend/tests/test_real_data_contract.py`
- `backend/tests/test_real_functionality.py`
- `frontend/tests/independent-audit.spec.ts`
- `frontend/tests/layout.spec.ts`
- `frontend/tests/mock-audit.spec.ts`
- `frontend/tests/pipeline-versions.spec.ts`
- `frontend/tests/real-data-system.spec.ts`

### Reproducible audit runners (4)

- `scripts/audit/generate_real_data.py`
- `scripts/audit/pipeline_persistence.py`
- `scripts/audit/read_export.py`
- `scripts/audit/real_data_system.py`

### Curated test fixtures (12)

- `tests/fixtures/e2e/customers.csv`
- `tests/fixtures/e2e/duplicate_records.csv`
- `tests/fixtures/e2e/empty_dataset.csv`
- `tests/fixtures/e2e/expected.json`
- `tests/fixtures/e2e/invalid_encoding.csv`
- `tests/fixtures/e2e/invalid_types.csv`
- `tests/fixtures/e2e/large_dataset.csv`
- `tests/fixtures/e2e/malformed.csv`
- `tests/fixtures/e2e/manifest.json`
- `tests/fixtures/e2e/provinces.csv`
- `tests/fixtures/e2e/unsupported.txt`
- `tests/fixtures/e2e/zero_bytes.csv`

### Documentation (13)

- `README.md`
- `docs/API.md`
- `docs/Architecture.md`
- `docs/BUG_FIX_REPORT.md`
- `docs/E2E_TEST_PLAN.md`
- `docs/E2E_TEST_RESULTS.md`
- `docs/FEATURE_VERIFICATION_MATRIX.md`
- `docs/FUNCTIONAL_VERIFICATION.md`
- `docs/MOCK_DATA_AUDIT.md`
- `docs/PRE_COMMIT_REVIEW.md`
- `docs/PipelineVersionVerification.md`
- `docs/REMAINING_ISSUES.md`
- `docs/UserGuide.md`

## Explicit exclusions

- Root E2E_TEST_RESULTS.md and FEATURE_VERIFICATION_MATRIX.md: duplicate delivery copies, preserved locally.
- work/, .dataguard/, raw/data/upload/export/temp managed storage, user database files and backups.
- Browser binaries, node_modules, virtual environments, build outputs, caches, test-results, Playwright reports, local logs, screenshots and generated CSV/Parquet downloads.
- Credentials, private keys and private .env files; safe .env.example remains tracked.
- The review's machine-readable file manifest, scan receipts, temporary port5173 adapter and report bundle are local artifacts under ignored work/ or outside the repository. They are not application source.

## Proposed commit message

```text
fix: harden DataGuard pipelines and data quality workflows

- preserve independent pipelines, immutable versions and concurrent-save safety
- validate imports and plans, retain quality snapshots and clarify execution status
- improve loading, error handling and responsive controls
- add isolated real-data and restart regression coverage
- exclude local data, credentials, browser binaries and generated artifacts
```

## Remaining limits and approval

Native Safari, Firefox, Windows, the default256 MiB upload-boundary stress case and peak-memory measurements were not rerun/tested. WebKit does not establish native Safari compatibility. Existing local-only authentication/settings/gate limitations are documented in the feature matrix. An unknown future input may still reveal a defect; the readiness decision is scoped to this diff and executed checks.

Approval is required before staging/committing. Push authorization is separate. Nothing has been staged, committed or pushed by this review.
