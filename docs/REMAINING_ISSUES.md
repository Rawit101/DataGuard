# Remaining issues and verification limits

Updated 2026-10-13 (Asia/Bangkok).

No known critical failing supported workflow or active production mock-data fallback remains from this audit. The final test results are 80 backend cases, 2 frontend unit tests, and 21 browser tests in each of Chromium and Playwright WebKit. Lint, TypeScript and production build pass. There is no blocked check caused by missing credentials for an existing feature: the implemented MVP uses local files and local storage.

| Area | Status | Limit / required next step |
| --- | --- | --- |
| Supported local workflows | PASS — Verified working with real data | Test evidence is in `FUNCTIONAL_VERIFICATION.md`; it covers the exercised fixtures and conditions, not all possible file sizes or every SQL expression. |
| Legacy quality history | PASS — Verified working with real data | Schema-2 records have no original rule snapshot. Counts/examples/files are preserved and the UI labels missing metadata. Past definitions cannot be reconstructed reliably; future executions store snapshots. |
| Native Safari application | NOT TESTED | Playwright WebKit passed. This is not a claim that the installed Safari app, browser UI zoom, or native popup arrow interaction was manually verified. Type-ahead keyboard selection was verified; popup arrow automation behaved the same on an unstyled baseline. |
| Firefox and Windows | NOT TESTED | Run the browser/workflow suites on those systems before making compatibility claims. |
| Enlarged rendering | PASS — Verified working with real data | The layout regression uses CSS zoom 1.25. Native browser UI zoom and OS accessibility text scaling are not covered. |
| Remote/multi-user deployment/authentication | NOT TESTED — Feature not implemented | The MVP assumes one trusted local user and one app instance per workspace. It has no accounts, roles or remote deployment contract. Loopback, origin, managed-path and SQL restrictions are not a replacement for remote authentication. |
| External databases/cloud integrations | NOT TESTED — Feature not implemented | No advertised connector or credential-dependent integration exists to verify. A future connector needs implementation, explicit credentials and its own real service tests. |
| Scheduled monitoring/outbound alerts/charts/global quality score | NOT TESTED — Feature not implemented | Current features are manually executed rules, stored results, run polling, strict quality gates and file reports. No fake score, chart, schedule or notification fills these gaps. |
| Runtime settings editing | NOT TESTED — Feature not implemented | Settings & Help is read-only. Configuration comes from environment values before startup; `.env.example` is not automatically loaded. |
| Discarded staged uploads | NOT TESTED — Automatic expiry not implemented | Discard removes the UI preview and never creates a dataset. Uncommitted staged files can remain in `tmp/`; a future managed expiry/discard cleanup should account for active imports. |
| Pipeline gate definitions | PASS — Verified working with real data | Saved pipeline versions retain rule IDs; new executions evaluate the current referenced rule configuration. Completed run outcomes stay stored. Referenced rules cannot now be deleted. Versioning the rules themselves is a separate future feature. |
| Very large files/resource limits | NOT TESTED in this audit | Upload size, memory and SQL limits are implemented; earlier large-file reports are historical evidence. This audit did not rerun the previous million-row benchmark or test every machine capacity. |
| Dependencies | PASS — Verified working with real data | One upstream Starlette/AnyIO deprecation warning remains. Pip reported an unwritable cache and disabled it; installed dependency consistency passed. Neither prevented tests or startup. |

The synthetic tutorial project and test fixtures remain intentionally available. They are explicit inputs to real processing, and never replace a failed backend response. No user data was deleted, no Git history was rewritten, and the audit changes have not been committed or pushed.
