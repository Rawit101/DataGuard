# DataGuard user guide

## Sample workflow

1. Start the app and choose **Load sample project** on Overview. The project contains the `property_listings` and `province_master` datasets, five warning quality rules, and a saved pipeline.
2. Open Datasets. Select each dataset to see the original filename, content hash, row count, schema, bounded row preview, exact profile, and Import issues. The listings fixture has 12 accepted rows. Use column sorting, text filtering, and page controls in the explorer.
3. Open Data Quality and run the included rules. The expected failed row counts are: unique listing ID 2, price parsing 1, date parsing 1, province reference 1, required listing ID 0. Click a run to inspect bounded examples and download all failing records as CSV. Warning severity allows the sample pipeline to finish.
4. Open Pipeline Builder, choose **Clean and summarize listings**, and select **Preview**. The preview executes the whole transformation plan before showing a bounded result. Inspect **Executed SQL** for the same SQL used for execution. Each step can be edited as JSON, reordered, disabled, removed, or added from a template. Choose **Save version** after edits.
5. Choose **Run saved version**. Runs & Exports shows queued, running, and completed states, per-step counts, quality outcomes, join diagnostics, lineage, and the output download. The sample left join leaves an unmatched code as `Unknown`.
6. Download the Parquet output. It has five province groups. Bangkok has `listing_count=2` and `avg_price_per_sqm=75000`; the other groups are Chonburi 2/50000, Nonthaburi 1/50000, Samut Prakan 1/100000, and Unknown 1/80000.
7. Stop and restart the app. Select the saved project and pipeline. Projects, datasets, rules, queries, versions, runs, and artifacts are stored in the workspace.

## Import and explore your own data

Create a project on Overview. Rename the selected project there with **Rename project**. On Datasets, select a CSV or Parquet file and choose **Inspect file**. For CSV, set header, delimiter, quote character, encoding, and null tokens before inspection. Review inferred types and sample rows, then choose a dataset name, SQL alias, and any type overrides before committing. The uploaded bytes are copied unchanged to managed raw storage. CSV snapshots keep values as text so failed type parsing cannot erase a value; the declared type and each parse issue are recorded. A structurally malformed record is excluded from the queryable snapshot, retained in the immutable raw file, and listed under Import issues with its content, physical line, and reason. If CSV syntax prevents safe recovery, the unread tail is recorded as rejected. The import rejects more than 100,000 issues rather than dropping further issues silently.

The explorer and SQL results are paged; page size is at most 100. Profiling uses exact counts, nulls, distinct values, min/max, and numeric summaries where a numeric type was declared. The import type inference uses the first 100 accepted rows, so inspect type overrides and parse issues on mixed data.

## SQL and exports

Use dataset aliases shown in SQL Workspace. SELECT statements, CTEs, joins, aggregates, and supported window functions work. Save named queries per project. Only one read-only statement over approved project aliases is accepted; file-reading functions, extension loading, and writes are rejected. The query page shows a bounded slice and a full result count. Export queues a background job for the full query result, in CSV or Parquet. The Runs & Exports page shows progress and download links. Choose UTF-8 BOM for a CSV intended for spreadsheet software with Thai text. CSV is text and applications may infer dates, numeric values, leading zeros, and nulls differently when reopened; Parquet preserves types.

## Quality and pipeline semantics

Available rules are required, composite uniqueness, parse validity, range, accepted values, regex, foreign key, and row count. Nulls are ignored by parse, range, accepted, regex, foreign-key, and unique rules; add a required rule if nulls are invalid. Required can also treat blank strings as missing. A duplicate rule counts every row in a duplicate group as failed. A row-count rule reports one failed dataset-level check, without inventing failing row IDs. Examples are bounded to 20, while the failure download contains all failing rows.

Pipeline steps include select/drop, rename, trim/case, fill/drop missing, cast with error/null/drop policies, deterministic deduplication, structured filters and calculations, joins, group-by, and sort. A division calculation returns null when its divisor is zero. Join null keys do not match. The join log reports duplicate reference keys, unmatched left rows, and null left keys; duplicate reference keys can multiply rows. Left and reference source IDs are retained through joins. Aggregated output carries `__source_count` rather than claiming a single source row. Pre and post quality checks run with a saved pipeline version. Under strict quality policy, error-severity failures prevent output publication; warnings stay visible. A failed or cancelled run has no completed output.

Use **Download runnable SQL** in Pipeline Builder to inspect or rerun the generated SQL. The script explains how to register the imported dataset Parquet snapshots. Use the app's run action for its managed quality checks, lineage, and atomic artifact publication.
