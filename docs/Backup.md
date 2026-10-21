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
- row counts and `schema_version` compared with the source
- SHA-256 and byte size of every archive member compared with the manifest

The archive contains `manifest.json` with the creation time, source path, schema
version, per-table row counts and per-file hashes. A failed check exits non-zero
and leaves no archive behind.

## Retention

`--keep N` keeps only the newest N archives in the output directory; older ones
are printed and deleted.

```sh
.venv/bin/python scripts/backup.py --keep 7
```

The default keeps every archive. Point `--output` at a directory that is itself
backed up off the machine if the data matters.

## Restore

Stop the app first, then replace the workspace directory with the archive
contents:

```sh
tar -xzf work/backups/dataguard-backup-20261021-173838.tar.gz -C /tmp/restore
# Inspect /tmp/restore/dataguard-workspace/manifest.json, then:
rm -rf work/full-system-workspace
mv /tmp/restore/dataguard-workspace work/full-system-workspace
DATAGUARD_WORKSPACE="$PWD/work/full-system-workspace" .venv/bin/python scripts/start.py
```

Restoring an older archive replaces newer projects and runs, so keep the current
workspace (or another backup) until the restored one has been checked.

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
