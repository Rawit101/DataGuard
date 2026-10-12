# End-to-end verification — 11 October 2026

## Browser workflow

Using the local app at `http://127.0.0.1:8000`, I created a separate **End-to-end verification** project and imported `property_listings.csv` and `province_master.csv` through the file picker. The listings import accepted 12 rows and recorded two type parse issues: `not-a-date` and `not-a-number`. The raw values remained visible. The province reference imported four rows. Dataset preview, issue details, and exact profile loaded.

I ran and saved a read-only join/aggregate SQL query. It returned five province groups. I created a warning quality rule for numeric price parsing; it evaluated 12 rows, failed one, showed the failing record, and offered a failure CSV download. A structured 12-step pipeline then trimmed text, removed missing/invalid values, deduplicated listing IDs, filtered non-positive area, left joined the province reference, filled the unmatched name, calculated price per square metre, aggregated, and sorted. Preview used the full relation before limiting display rows. Join diagnostics reported one unmatched key (`XXX`) and no duplicate reference keys.

Pipeline version 1 completed with a five-row Parquet output; version 2 completed with a five-row CSV output and UTF-8 BOM. Both artifact files were read back and compared with these expected `(province, listing count, average THB/sqm)` values: Bangkok `(2, 75000)`, Chonburi `(2, 50000)`, Nonthaburi `(1, 50000)`, Samut Prakan `(1, 100000)`, and Unknown `(1, 80000)`. The Parquet browser download event fired; the CSV browser download endpoint returned HTTP 200. After restarting the app, both completed runs and artifacts remained visible. A browser SQL attempt to call `read_csv('/etc/passwd')` returned HTTP 400 with a readable rejection message; the saved approved query still ran.

## Defects found and fixed

- The dataset profile table had shown text-based min/max values for declared numeric and date columns and hid the numeric summary fields. The API now computes min, max, mean, and invalid counts from valid parsed values, and the UI displays them. The browser showed price min/max `2,000,000`/`9,000,000`, mean `5,300,000`, one invalid price, and one invalid date.
- Dataset profile and import-issue tabs briefly showed an empty-state message before their responses arrived. They now show explicit loading states.
- Runs & Exports polled continuously after all jobs were completed. It now polls only while a run or export is queued or running. The backend log stayed idle after completed runs loaded.

## Automated checks and limits

- Backend: `12 passed` with one dependency deprecation warning from Starlette/AnyIO.
- Frontend: 2 unit tests passed; lint, TypeScript check, and production build passed.
- Browser console: no warnings or errors observed during the final run. Backend request log: no unexpected 5xx responses; the tested forbidden SQL returned the expected 400.
- The automated Playwright Chromium suite remains unverified because Chromium startup is denied by this macOS sandbox. The workflow above was exercised manually in the in-app browser. Windows launch remains untested here.
