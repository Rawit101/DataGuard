# Architecture and decisions

## Data flow

```text
Browser (React/Vite) → FastAPI → SQLite metadata
                           ├→ immutable raw uploads
                           ├→ DuckDB-approved Parquet snapshots
                           └→ queued jobs → one worker process → atomic export artifact
```

`backend/app/main.py` owns API contracts and request handling. `db.py` initializes metadata and migrates schema version 1 to 2. `imports.py` stages files, inspects types, records malformed CSV and parse issues, and creates managed snapshots. `engine.py` owns project-scoped DuckDB connections, restricted SQL, paging, profiling, and SQL exports. `quality.py` evaluates rules and records outcomes. `pipeline.py` compiles versioned JSON steps to DuckDB SQL and computes join diagnostics. `worker.py` runs queued pipeline and export jobs serially. `frontend/src` owns the seven UI sections and typed request helpers.

SQLite is the durable metadata store. `.dataguard/raw` holds immutable uploaded bytes; `data` holds queryable Parquet snapshots with `__source_row_id`; `tmp` holds staged uploads and unpublished outputs; `exports` holds published artifacts. File names in storage use generated IDs. Downloads resolve artifact IDs from SQLite and verify that the path remains inside the managed export directory. A run writes to `tmp`, then moves its result to `exports` only after processing and strict checks succeed. The worker repairs interrupted statuses on startup. Run records reference an immutable pipeline version and input content hashes.

## Deliberate MVP boundaries

- **SQL:** user statements are parsed as one SELECT/CTE query. Tables must be approved aliases or local CTEs, functions come from an allowlist, and writes/file functions are rejected. An isolated DuckDB connection registers only the project's snapshot views, restricts paths to those files, disables external access and extension loading, and locks the configuration. Trusted import and pipeline code use separate connections. The app is for one trusted local user; this is defense in depth, not a security sandbox against arbitrary hostile local code.
- **Jobs:** one worker subprocess polls SQLite and executes one pipeline or SQL export at a time. This avoids sharing DuckDB connections across threads or concurrent writers. API requests still use their own connections for reads and interactive previews. Cancellation is a persisted flag plus DuckDB interrupt; uncommitted files are removed. Query and export timeouts are bounded. SQL export time limit is ten times the interactive query limit.
- **CSV issues:** the original bytes are retained. Valid structural records enter the snapshot as text. Invalid records and type parse failures are recorded with source location/value and reason; a syntax error causes the unread tail to be recorded as rejected because record boundaries may be unknown. No malformed row is silently discarded.
- **Previews:** the same compiled SQL powers preview and full run. Preview limits only the final result set after the complete relation is evaluated, so aggregate, join, and deduplication results remain meaningful. It may still perform full computation.
- **Lineage:** source row IDs survive row-level steps. A join retains both sides' IDs. Group-by replaces per-row identity with `__source_count`. Run lineage records the input dataset IDs and hashes, pipeline version, output artifact ID, and join diagnostics.

## Pipeline definition

Definitions are JSON objects with `source_dataset_id`, `steps`, optional `pre_rule_ids` and `post_rule_ids`, `strict_quality`, and `output` (`format`, `filename`, optional `utf8_bom`). Each step has a `type` and type-specific fields, with optional `enabled`. `pipeline.py` validates columns against the schema after every preceding step and emits SQL plus step descriptions. The definitions are versioned in SQLite. The compiler boundary is the future execution-engine seam: another engine can consume the same validated operation list and return a plan, while run orchestration and quality policy remain separate.

No cache of computed outputs is used in this MVP. Saved dataset snapshots are immutable and identified by hash, which gives a sound basis for later cache keys that include source hash and pipeline version. The application uses one process per API and worker; running multiple app servers against the same workspace is unsupported.
