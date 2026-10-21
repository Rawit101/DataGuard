"""Create a consistent, verified backup of a DataGuard workspace.

The SQLite database is copied through the sqlite3 backup API, so the snapshot is
consistent even while the application is running. Raw uploads, canonical data and
exports are copied byte for byte. The resulting archive is reopened and every
member is checked against the manifest before the command reports success.

Example:
    .venv/bin/python scripts/backup.py
    .venv/bin/python scripts/backup.py --workspace work/full-system-workspace --keep 7
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("raw", "data", "exports")
ARCHIVE_PREFIX = "dataguard-workspace"


def resolve_workspace(value: str | None) -> Path:
    candidate = value or os.environ.get("DATAGUARD_WORKSPACE") or ROOT / ".dataguard"
    workspace = Path(candidate).expanduser().resolve()
    if not (workspace / "metadata.sqlite3").is_file():
        raise SystemExit(f"Not a DataGuard workspace (no metadata.sqlite3): {workspace}")
    return workspace


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def database_summary(path: Path) -> dict:
    with sqlite3.connect(path) as db:
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return {"schema_version": int(row[0]) if row else None, "table_counts": counts}


def copy_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)


def verify_database(copy: Path, expected: dict) -> dict:
    with sqlite3.connect(f"file:{copy}?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"Database backup failed integrity_check: {integrity}")
    actual = database_summary(copy)
    if actual != expected:
        raise SystemExit(f"Database backup does not match source: {actual} != {expected}")
    return actual


def collect(workspace: Path, staging: Path) -> list[dict]:
    entries = []
    database = staging / "metadata.sqlite3"
    copy_database(workspace / "metadata.sqlite3", database)
    entries.append({"path": "metadata.sqlite3", "bytes": database.stat().st_size,
                    "sha256": sha256(database)})
    for category in CATEGORIES:
        for source in sorted((workspace / category).rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(workspace)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            entries.append({"path": relative.as_posix(), "bytes": target.stat().st_size,
                            "sha256": sha256(target)})
    return entries


def write_archive(staging: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            tar.add(path, arcname=f"{ARCHIVE_PREFIX}/{path.relative_to(staging).as_posix()}",
                    recursive=False)


def verify_archive(archive: Path, entries: list[dict]) -> None:
    expected = {f"{ARCHIVE_PREFIX}/{entry['path']}": entry for entry in entries}
    expected[f"{ARCHIVE_PREFIX}/manifest.json"] = None
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            if member.name not in expected:
                raise SystemExit(f"Unexpected archive member: {member.name}")
            entry = expected[member.name]
            seen.add(member.name)
            if entry is None:
                continue  # manifest.json is described by the archive itself
            handle = tar.extractfile(member)
            assert handle is not None
            digest = hashlib.sha256(handle.read()).hexdigest()
            if digest != entry["sha256"] or member.size != entry["bytes"]:
                raise SystemExit(f"Archive member failed verification: {member.name}")
    missing = set(expected) - seen
    if missing:
        raise SystemExit(f"Archive is missing members: {sorted(missing)}")


def prune(output: Path, keep: int) -> list[Path]:
    if keep <= 0:
        return []  # default: keep every archive
    archives = sorted(output.glob("dataguard-backup-*.tar.gz"))
    removed = archives[:-keep]
    for path in removed:
        path.unlink()
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up a DataGuard workspace.")
    parser.add_argument("--workspace", help="workspace directory (default: DATAGUARD_WORKSPACE or .dataguard)")
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "backups",
                        help="directory for the archive (default: work/backups)")
    parser.add_argument("--keep", type=int, default=0,
                        help="keep only the newest N archives in the output directory (default: keep all)")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    created = datetime.now().astimezone()
    stamp = created.strftime("%Y%m%d-%H%M%S")
    archive = output / f"dataguard-backup-{stamp}.tar.gz"
    counter = 1
    while archive.exists():
        # Two runs in the same second must not overwrite each other.
        counter += 1
        archive = output / f"dataguard-backup-{stamp}-{counter}.tar.gz"

    with tempfile.TemporaryDirectory(dir=output, prefix=".staging-") as temporary:
        staging = Path(temporary) / ARCHIVE_PREFIX
        staging.mkdir()
        source = database_summary(workspace / "metadata.sqlite3")
        entries = collect(workspace, staging)
        verified = verify_database(staging / "metadata.sqlite3", source)
        manifest = {
            "created_at": created.isoformat(),
            "source_workspace": str(workspace),
            "schema_version": verified["schema_version"],
            "table_counts": verified["table_counts"],
            "file_count": len(entries),
            "total_bytes": sum(entry["bytes"] for entry in entries),
            "files": entries,
            "restore": f"Stop the app, extract {ARCHIVE_PREFIX}/ over the workspace directory, then start the app.",
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        write_archive(staging, archive)
        verify_archive(archive, entries)

    removed = prune(output, args.keep)
    size = archive.stat().st_size
    print(f"Workspace: {workspace}")
    print(f"Archive:   {archive}")
    print(f"Files:     {manifest['file_count']} ({manifest['total_bytes']:,} bytes)")
    print(f"Schema:    v{manifest['schema_version']}, {sum(manifest['table_counts'].values())} rows "
          f"across {len(manifest['table_counts'])} tables")
    print(f"Verified:  database integrity, row counts, and archive hashes")
    print(f"Size:      {size:,} bytes")
    if removed:
        print("Pruned:    " + ", ".join(path.name for path in removed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
