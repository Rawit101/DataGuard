# Backup and restore

DataGuard keeps everything for a workspace in one directory: the SQLite metadata
database plus the managed `raw/`, `data/` and `exports/` files. A backup is a
single compressed archive of that directory, so restoring is a directory copy.

## Create a backup

```sh
.venv/bin/python scripts/backup.py
```

By default this backs up `DATAGUARD_WORKSPACE`, falling back to `.dataguard/`, and
writes `work/backups/dataguard-backup-<timestamp>.tar.gz`. To pick another
workspace or output directory:

```sh
.venv/bin/python scripts/backup.py --workspace work/full-system-workspace --output ~/Backups/dataguard
```

The command is safe to run while the app is open. The SQLite database is copied
through the sqlite3 backup API, which produces a consistent snapshot even if a
query or pipeline is writing at that moment, and the archive is reopened and
checked before the command reports success:

- `PRAGMA integrity_check` on the copied database
- row counts and `schema_version` read from that same copied database, so live
  writes cannot cause a comparison between different snapshots
- SHA-256 and byte size of every archive member compared with the manifest

The archive contains `manifest.json` with the creation time, source path, schema
version, per-table row counts and per-file hashes. The archive is created inside
a temporary staging directory and receives its final filename only after
verification succeeds. Errors, Ctrl-C, and SIGTERM remove the incomplete staging
directory and archive; existing backups are retained. An uncatchable termination (such as
SIGKILL or power loss) can leave a hidden `.staging-*` directory, but cannot
publish its incomplete archive as a completed backup.

## Retention

`--keep N` keeps only the newest N archives in the output directory; older ones
are printed and deleted.

```sh
.venv/bin/python scripts/backup.py --keep 7
```

Retention runs only after a verified archive has been successfully published.
The default keeps every archive. Point `--output` at a directory that is itself
backed up off the machine if the data matters.

## Restore

Stop the app first and keep it stopped through validation and the directory
switch. Use the original workspace path from `manifest.json`: metadata contains
absolute managed-file paths, so moving a restored workspace to a different path
is not supported by this procedure. Run the following blocks in the same terminal
and continue only when each block succeeds.

Extract to a new temporary directory. Adjust the archive and workspace values:

```sh
restore_workspace="$PWD/work/full-system-workspace"
restore_archive="$PWD/work/backups/dataguard-backup-20261021-173838.tar.gz"
restore_scratch="$(mktemp -d "${TMPDIR:-/tmp}/dataguard-restore.XXXXXX")"
tar -xzf "$restore_archive" -C "$restore_scratch"
```

Validate the extracted files, SQLite integrity, schema, and table counts before
moving the current workspace. This also rejects an incorrect destination path:

```sh
# Validate the extracted snapshot before moving any workspace.
.venv/bin/python - "$restore_scratch/dataguard-workspace" "$restore_workspace" <<'PY'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
workspace = Path(sys.argv[2]).resolve()
manifest = json.loads((root / "manifest.json").read_text())
if Path(manifest["source_workspace"]).resolve() != workspace:
    raise SystemExit("Restore destination must match manifest source_workspace")
for entry in manifest["files"]:
    path = (root / entry["path"]).resolve()
    if not path.is_relative_to(root):
        raise SystemExit("Manifest path escapes the extracted workspace")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if path.stat().st_size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
        raise SystemExit(f"File verification failed: {entry['path']}")
with sqlite3.connect((root / "metadata.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Database integrity check failed")
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
              for name in tables}
    schema = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if counts != manifest["table_counts"] or not schema or int(schema[0]) != manifest["schema_version"]:
        raise SystemExit("Database summary does not match manifest")
print("Extracted workspace verified; the original has not been moved")
PY
```

After validation succeeds, move the current workspace aside, then place the
restored copy at the original path. Neither workspace is deleted:

```sh
# Preserve the original workspace and activate the validated copy.
restore_previous="${restore_workspace}.before-restore-$(date +%Y%m%d-%H%M%S)"
test ! -e "$restore_previous" &&
    mv "$restore_workspace" "$restore_previous" &&
    mv "$restore_scratch/dataguard-workspace" "$restore_workspace"
```

If either move fails, stop and inspect the paths before starting the app. The
original workspace remains at its original path or at `restore_previous`.
When both moves succeed, launch the restored workspace:

```sh
DATAGUARD_WORKSPACE="$restore_workspace" .venv/bin/python scripts/start.py
```

Check projects, datasets, saved pipelines, and existing downloads in the app.
Restoring an older archive replaces the active view of newer projects and runs;
keep `restore_previous` until these checks pass. If application validation fails,
stop the app and switch back while retaining the rejected copy for inspection:

```sh
# Roll back after stopping the restored app, preserving both copies.
restore_rejected="${restore_workspace}.rejected-$(date +%Y%m%d-%H%M%S)"
test ! -e "$restore_rejected" &&
    mv "$restore_workspace" "$restore_rejected" &&
    mv "$restore_previous" "$restore_workspace"
```

## Scheduled backups

Because this is a single-user local app, a launchd job or cron entry is usually
enough. Example cron entry running daily at 02:17:

```cron
17 2 * * * cd /Users/ssrawit/Documents/DataGuard && .venv/bin/python scripts/backup.py --workspace work/full-system-workspace --output "$HOME/Backups/dataguard" --keep 14 >> work/backups/backup.log 2>&1
```

## What is not backed up

- `tmp/` working files, which are rebuilt as needed
- rebuildable artifacts: the virtualenv, `node_modules/`, the frontend build and
  Playwright browsers
- `exports/` **is** included, because exported files are user-visible results
