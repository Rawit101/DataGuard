# Real-data feature verification matrix

15 October 2026. Evidence root: `work/real-data-e2e-evidence/`. PASS applies to the executed scenarios below, not every possible input. The final browser logs contain the exact test names and results. Reproduction commands are in [E2E_TEST_RESULTS.md](E2E_TEST_RESULTS.md).

Evidence keys:

- **C**: [Chromium complete suite](evidence/chromium/browser-before-restart.log)
- **W**: [WebKit complete suite](evidence/webkit/browser-before-restart.log)
- **R**: [Chromium actual-restart suite](evidence/chromium/browser-after-restart.log) and [WebKit actual-restart suite](evidence/webkit/browser-after-restart.log)
- **DB**: [direct SQLite/file assertions](evidence/chromium/database-assertions.json), [complete pre-restart snapshot](evidence/chromium/database-before-restart.json), [post-restart snapshot](evidence/chromium/database-after-restart.json)
- **B**: [backend results](evidence/backend.log)
- **E**: [fixture oracle](fixtures/expected.json), derived with Python csv/Counter independently of DataGuard/DuckDB

| Feature | Test scenario | Expected result | Actual result | Evidence | Status |
|---|---|---|---|---|---|
| Project creation | Two projects created through real UI | Unique IDs, selected project persists | Independent records, selected ID survives reload | C/W real CSV scenario; DB projects | PASS |
| Project rename | UI rename then reload/restart | Updated name retained | Renamed name restored after process restart | C/W/R; DB | PASS |
| Project resources | Switch to second empty project | No datasets/pipelines/runs from first | Catalog empty and all three KPIs 0 | C/W real CSV scenario; DB | PASS |
| Upload bytes | Upload six physical CSVs via file chooser | Managed originals identical to fixtures | All six byte comparisons and SHA-256 match | DB; fixture manifest | PASS |
| Customer preview | Import customers.csv | 6 rows, exact names/provinces/amounts/nulls | All six dictionaries compared | C/W real CSV scenario; E | PASS |
| Schema inference | Inspect amount/customer columns | Inferred/declared types visible, original text preserved | amount BIGINT declaration; stored text verified | C/W; dataset-profile screenshot | PASS |
| Profile | Missing province | 2 null rows | Exact profile null_count=2 | C/W; profile screenshot | PASS |
| Empty CSV dataset | Header-only CSV | Valid dataset, 0 records | Imported and row-count rule detects empty dataset | C/W; DB | PASS |
| Malformed CSV | Wrong column count + unterminated quote | Accepted rows preserved; rejected tail/reasons retained | 2 accepted, 2 issues; unread tail contains final line | C/W invalid-import scenario; import_rejects snapshot | PASS |
| Invalid encoding | Actual undecodable UTF-8 bytes | Meaningful HTTP400 | Selected encoding and decode reason displayed | C/W; B encoding regression | PASS |
| Upload validation | Unsupported extension, zero bytes | Actionable HTTP400 | Both shown in actual UI | C/W invalid-import scenario | PASS |
| Upload cap | 4 MiB isolated cap, cap+1 byte request | Request rejected, no dataset published | HTTP400 upload limit | C/W invalid-import scenario; environment.json | PASS |
| Large CSV | 50,000 Thai rows | 50,000 records; sum 24,836,625 | HTTP import + SQL + direct Parquet agree | C/W; DB | PASS |
| SQL count/distinct | Customer COUNT/COUNT DISTINCT | 6 total, 5 IDs | Exact returned values compared | C/W real CSV scenario; E | PASS |
| SQL SUM/AVG | Explicit casts of preserved raw text | 13,000 / 2166.6666666666665 | Exact backend values compared | C/W; E | PASS |
| SQL filtering/sorting/limit | amount >1500; ordered names; LIMIT4 | Mali, Mali, Nida, Suda | All records match | C/W; E | PASS |
| SQL grouping | Group province including NULL | NULL2, Bangkok2, Chiang Mai1, Phuket1 | All groups/counts match | C/W; E | PASS |
| SQL JOIN | Customers/provinces inner join | Four matched records and mapped regions | Exact names/regions match | C/W; E | PASS |
| SQL empty result | Absent customer ID | Empty real result, no invented rows | rows=[] and result UI visible | C/W | PASS |
| SQL errors | Syntax, unknown dataset/column | HTTP400, visible errors, data intact | All rejected; catalog unchanged | C/W invalid-import scenario | PASS |
| SQL security | DELETE, multiple statements, file function | Read-only restriction | HTTP400 for each | C/W; B boundary tests | PASS |
| Query limits | 50,001+ chars; expensive approved cross join | Rejection; actual 2-second interruption | HTTP400 and interrupt message | C/W boundary scenario; B | PASS |
| Saved SQL | UI save/edit/reload/delete | Real persisted update/deletion | Saved query text restored then deletion confirmed | C/W | PASS |
| Required province | Create/run real Error-severity rule | 6 evaluated, 2 failed, 33.3% | Counts, row IDs4/5, two-row failure download match | C/W; DB quality_runs | PASS |
| Duplicate occurrences | Explicit beyond-first policy | 1 extra row, source row5, 16.7% | Backend/UI/history agree | C/W; quality-one-duplicate screenshot; B | PASS |
| Legacy uniqueness | Default all-group policy | Both rows in duplicate pair fail | 2 failures, existing policy retained | C/W; B | PASS |
| Positive amount | Error-severity range rule | 0 failures, PASSED | Error severity neutral; execution PASSED | C/W; quality-passed-error-severity screenshot | PASS |
| Quality rule kinds | Parse/range/accepted/regex/FK/row count | Actual failing counts per fixture | Parse1, accepted1, regex0, FK0, empty row-count1 | C/W; B all-rule tests | PASS |
| Quality edit/delete/history | Disposable duplicate rule update/delete; required history reload | Real stored history; supported mutations succeed | Snapshot/counts survive reload/restart | C/W/R; DB | PASS |
| Quality execution errors | Actual incompatible foreign-key types | ERROR with no invented failure count | HTTP400, ERROR card; no completed quality run | C/W pipeline-version severity scenario | PASS |
| Pipeline transformations | Cast/filter/deduplicate/select/join/rename/sort | Step rows6/4/3/3/3/3/3 | Real preview and completed worker log match | C/W; runs-real-output screenshot; DB | PASS |
| CSV output | Download joined pipeline CSV | 3 exact records, all fields/lineage match | Python CSV decoder compared every column/value | C/W; cleaned-customers.csv; E | PASS |
| Parquet output | Download warning-gated pipeline Parquet | Same 3 records with typed amount/nulls | Decoded all records and lineage match | C/W; cleaned.parquet; E | PASS |
| SQL exports | Full six-row CSV/Parquet + Thai CSV | Every cell correct; UTF-8 BOM requested | Python CSV/Parquet readback matches | C/W; customers-full files; thai-results.csv | PASS |
| Error quality gates | Pre and post required-province failures | Failed runs, no output publication | Both failed, output_artifact_id NULL, no artifact rows | C/W; DB | PASS |
| Warning gate | Warning failure + passing post rule | Complete with visible quality outcomes | Quality statuses failed/passed, output valid | C/W; DB | PASS |
| Five pipelines | New/save five via UI | Five unique IDs, all visible/executable | All five present; saved definitions execute correctly | C/W pipeline-version scenario; five-pipelines.json | PASS |
| Pipeline versions | Update only p01; history/restore/duplicate | Other four/sentinel unchanged; immutable versions | v1/v2/v3 accessible; independent duplicate ID | C/W; DB pipeline_versions | PASS |
| Concurrent save | Synchronized writers + stale UI | One accepted, stale409, drafts/history safe | Backend200/409 and UI draft retained | B; C/W stale-save scenario | PASS |
| Actual restart | New server PID, identical workspace | All tables/files unchanged; downloads usable | Exact full-row/hash equality, five pipelines execute again | R; both result.json files | PASS |
| Execution records | Real completed/failed pipelines | Timestamps, duration, steps, lineage, persisted history | Four main-project runs reconciled, retained after restart | DB; C/W/R | PASS |
| Cancellation/rerun | Real worker jobs and browser actions | Cancelled jobs publish no artifact; rerun saved version | Existing browser/backend scenarios pass | C/W independent audit; B | PASS |
| Overview metrics | UI → overview API → SQLite | 6 datasets, 4 pipelines, 4 recent runs | All three KPIs and four run IDs exact; empty project0/0/0 | C/W; DB; overview screenshot | PASS |
| Overview bounds | Recent runs implementation | Latest10 only, not total execution count | Inspected SQL LIMIT10; frontend uses returned length | main.py overview; C/W reconciliation | PASS |
| Settings | Real config response and process restart | Read-only values consistent | Actual workspace/cap/timeout displayed and retained | C/W/R; settings screenshot | PASS |
| Reliability | Actual browser offline then retry | Error state; recover real settings | Retry settings succeeds after network restored | C/W; offline-error screenshot | PASS |
| Traversal/isolation | Tokens/filenames, foreign project references | Safe rejection; no cross-project query | Invalid tokens/filename rejected; foreign dataset404, alias400 | C/W; B | PASS |
| Local request protection | Foreign Origin and Host | Reject unauthorized browser origins/hosts | HTTP403 and HTTP400 | C/W boundary scenario | PASS |
| Sensitive values | Targeted key/private-key signatures in responses/logs | No matching credentials | No matches within inspected scope; not a comprehensive secret audit | response assertions; sensitive-scan.json | PASS |
| Layout/accessibility | All pages at375/768/1024/1440/1920; long Thai/JSON | No document overflow; controls accessible | Existing layout/labels/locking regressions pass | C/W layout scenarios | PASS |
| Production preservation | Compare original DB and stored files | No user record or file changed | See final preservation report | user-preservation.json | PASS |
| Editable settings | Attempt persistent settings editing | Feature not implemented | Configuration comes from startup environment | Help/config inventory | NOT SUPPORTED |
| Project/dataset/pipeline delete | Supported-operations inventory | No destructive action assumed | No delete API/UI for these resources | API inventory | NOT SUPPORTED |
| Auth/multi-user authorization | Architecture inventory | Explicit capability statement | Local single-user app; no login/access-control model | main.py; settings local_only | NOT SUPPORTED |
| Extra dashboard KPIs/charts | Project total, quality rate, charts | Verify only implemented displays | These displays do not exist | Overview.tsx; API inventory | NOT SUPPORTED |
| Native Safari/Firefox/Windows | Platform coverage | Actual native tests needed | Chromium/WebKit engines on macOS only | Browser logs | NOT TESTED |
| Default256 MiB boundary/peak memory | Resource coverage | Dedicated large-boundary/RSS run | 4 MiB cap and50k fixture used; no peak memory measurement | environment.json | NOT TESTED |
