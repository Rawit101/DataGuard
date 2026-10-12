# Roadmap and current limits

## Current MVP limits

- One trusted local user and one app instance per workspace. There is no account system or remote deployment support.
- The pipeline editor uses typed step templates and JSON fields. A form-driven editor and graph canvas would make complex pipelines easier to build.
- CSV import snapshots retain accepted values as text to avoid losing malformed values; explicit pipeline casts create typed outputs. Declared import types drive profiling and parse-issue reports.
- SQL functions are intentionally limited to an allowlist. This may reject safe DuckDB functions until reviewed and added.
- Interactive SQL counts the full result for accurate pagination, which can be slow for large queries. Profile metrics are exact and can also take time.
- The app does not cache computed plans or exports, schedule runs, connect to external databases, detect schema drift, or execute Python/PySpark scripts.
- Resource limits and DuckDB restrictions protect ordinary local use; they are not a security sandbox for hostile code running on the same computer.

## Next development steps

1. Add a form-based step editor with schema-aware controls and clearer validation at each step.
2. Add a project-level operation queue and explicit idempotency keys for duplicate requests across network retries and multiple browser tabs.
3. Add resumable/chunked imports and streaming issue storage for files larger than the local upload limit.
4. Add schema drift checks, run comparisons, scheduled local runs, and notifications.
5. Add connector and execution-engine adapters, including an optional PySpark backend, while keeping SQL restrictions and artifact lineage explicit.
