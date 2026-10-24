import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app import worker
from scripts import backup

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, check=False)


def workspace_hashes(workspace):
    paths = [workspace / "metadata.sqlite3"]
    for category in backup.CATEGORIES:
        paths.extend(path for path in (workspace / category).rglob("*") if path.is_file())
    return {str(path.relative_to(workspace)): backup.sha256(path) for path in paths}


def invoke(monkeypatch, workspace, output, *args):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--workspace", str(workspace),
                                    "--output", str(output), *args])
    backup.main()


def restore_block(marker):
    blocks = re.findall(r"```sh\n(.*?)```", (ROOT / "docs/Backup.md").read_text(), re.S)
    return next(block for block in blocks if marker in block)


def restore_command(marker, **variables):
    return subprocess.run(["sh", "-c", restore_block(marker)], cwd=ROOT,
                          env={**os.environ, **{key: str(value) for key, value in variables.items()}},
                          capture_output=True, text=True, check=False)


def test_backup_creates_verified_archive(demo, tmp_path):
    before = workspace_hashes(tmp_path)
    output = tmp_path / "backups"
    result = run("--workspace", tmp_path, "--output", output)

    assert result.returncode == 0, result.stderr
    archives = list(output.glob("dataguard-backup-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as tar:
        manifest = json.load(tar.extractfile("dataguard-workspace/manifest.json"))
        assert manifest["schema_version"] == 3
        assert manifest["file_count"] == len(manifest["files"])
        assert sum(manifest["table_counts"].values()) > 0
        assert {"metadata.sqlite3"} <= {entry["path"] for entry in manifest["files"]}
        for entry in manifest["files"]:
            data = tar.extractfile(f"dataguard-workspace/{entry['path']}").read()
            assert len(data) == entry["bytes"]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    # The source database and managed files are read, never modified.
    assert workspace_hashes(tmp_path) == before
    assert not list(output.glob(".staging-*"))


def test_backup_retention_keeps_newest_archives(demo, tmp_path):
    output = tmp_path / "backups"

    for _ in range(3):
        result = run("--workspace", tmp_path, "--output", output)
        assert result.returncode == 0, result.stderr
    assert len(list(output.glob("dataguard-backup-*.tar.gz"))) == 3

    result = run("--workspace", tmp_path, "--output", output, "--keep", 2)
    assert result.returncode == 0, result.stderr
    assert len(list(output.glob("dataguard-backup-*.tar.gz"))) == 2
    assert "Pruned:" in result.stdout


def test_backup_rejects_non_workspace(tmp_path):
    result = run("--workspace", tmp_path / "missing", "--output", tmp_path / "backups")
    assert result.returncode != 0
    assert "Not a DataGuard workspace" in result.stderr


@pytest.mark.parametrize("phase,error", [
    ("copy_database", OSError),
    ("verify_database", SystemExit),
    ("write_archive", OSError),
    ("write_archive", KeyboardInterrupt),
    ("verify_archive", SystemExit),
    ("publication", OSError),
])
def test_failed_backup_cleans_staging_and_never_prunes(demo, tmp_path, monkeypatch, phase, error):
    output = tmp_path / "backups"
    assert run("--workspace", tmp_path, "--output", output).returncode == 0
    previous = {path.name: path.read_bytes() for path in output.iterdir()}
    source = workspace_hashes(tmp_path)

    def fail(*args):
        if phase in {"copy_database", "write_archive"}:
            Path(args[1]).write_bytes(b"incomplete output")
        raise error("injected backup failure")

    def unexpected_prune(*args):
        pytest.fail("Retention must not run after a failed backup")

    if phase == "publication":
        monkeypatch.setattr(Path, "replace", fail)
    else:
        monkeypatch.setattr(backup, phase, fail)
    monkeypatch.setattr(backup, "prune", unexpected_prune)
    with pytest.raises(error, match="injected backup failure"):
        invoke(monkeypatch, tmp_path, output, "--keep", "1")
    assert {path.name: path.read_bytes() for path in output.iterdir()} == previous
    assert workspace_hashes(tmp_path) == source


@pytest.mark.parametrize("corruption", ["changed_file", "missing_file"])
def test_real_archive_verification_failure_is_not_published(demo, tmp_path, monkeypatch, corruption):
    output = tmp_path / "backups"
    before = workspace_hashes(tmp_path)
    write = backup.write_archive

    def corrupt_staged_copy(staging, archive):
        path = next((staging / "raw").iterdir())
        if corruption == "changed_file":
            path.write_bytes(b"changed after manifest hashes were collected")
        else:
            path.unlink()
        write(staging, archive)

    monkeypatch.setattr(backup, "write_archive", corrupt_staged_copy)
    with pytest.raises(SystemExit, match="verification|missing members"):
        invoke(monkeypatch, tmp_path, output)
    assert list(output.iterdir()) == []
    assert workspace_hashes(tmp_path) == before


def test_manifest_uses_snapshot_counts_during_concurrent_writes(demo, tmp_path, monkeypatch):
    output = tmp_path / "backups"
    database = tmp_path / "metadata.sqlite3"
    copy = backup.copy_database

    def insert_project(identifier):
        # A separate thread and SQLite connection commit real live changes.
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO projects VALUES (?,?,?)", (identifier, identifier, "2026-10-24"))

    with ThreadPoolExecutor(max_workers=1) as writer:
        def copy_with_live_writes(source, destination):
            writer.submit(insert_project, "before-snapshot").result(timeout=5)
            copy(source, destination)
            writer.submit(insert_project, "after-snapshot").result(timeout=5)

        monkeypatch.setattr(backup, "copy_database", copy_with_live_writes)
        invoke(monkeypatch, tmp_path, output)

    with tarfile.open(next(output.glob("*.tar.gz"))) as tar:
        manifest = json.load(tar.extractfile("dataguard-workspace/manifest.json"))
        snapshot = tmp_path / "snapshot.sqlite3"
        snapshot.write_bytes(tar.extractfile("dataguard-workspace/metadata.sqlite3").read())
    assert manifest["table_counts"] == backup.database_summary(snapshot)["table_counts"]
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT id FROM projects WHERE id='before-snapshot'").fetchone()
        assert db.execute("SELECT id FROM projects WHERE id='after-snapshot'").fetchone() is None
    assert backup.database_summary(database)["table_counts"]["projects"] == manifest["table_counts"]["projects"] + 1


def test_retention_sees_only_verified_published_archive(demo, tmp_path, monkeypatch):
    output = tmp_path / "backups"
    verify = backup.verify_archive
    prune = backup.prune
    verified = []
    retained = []

    def observe_verification(archive, entries):
        assert archive.parent.name.startswith(".staging-")
        assert list(output.glob("dataguard-backup-*.tar.gz")) == []
        verify(archive, entries)
        verified.append(backup.sha256(archive))

    def observe_retention(directory, keep):
        published = list(directory.glob("dataguard-backup-*.tar.gz"))
        assert len(published) == 1
        assert [backup.sha256(published[0])] == verified
        assert not list(directory.glob(".staging-*"))
        retained.append(published[0])
        return prune(directory, keep)

    monkeypatch.setattr(backup, "verify_archive", observe_verification)
    monkeypatch.setattr(backup, "prune", observe_retention)
    invoke(monkeypatch, tmp_path, output, "--keep", "1")
    assert len(retained) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal process verification")
@pytest.mark.parametrize("termination", [signal.SIGINT, signal.SIGTERM])
def test_signal_during_archive_creation_cleans_unpublished_output(demo, tmp_path, termination):
    output = tmp_path / "backups"
    assert run("--workspace", tmp_path, "--output", output).returncode == 0
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    source = workspace_hashes(tmp_path)
    ready = tmp_path / "archive-started"
    code = """
import runpy
import sys
import tarfile
import threading
from pathlib import Path
ready = Path(sys.argv.pop(1))
original_open = tarfile.open
def interrupted_open(name, mode='r', *args, **kwargs):
    if mode == 'w:gz':
        Path(name).write_bytes(b'incomplete archive')
        ready.touch()
        threading.Event().wait(30)
    return original_open(name, mode, *args, **kwargs)
tarfile.open = interrupted_open
runpy.run_path('scripts/backup.py', run_name='__main__')
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(ready), "--workspace", str(tmp_path),
                                "--output", str(output), "--keep", "1"], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Backup did not reach archive creation"
        assert {path.name for path in output.glob("*.tar.gz")} == set(before)
        process.send_signal(termination)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 128 + termination, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert workspace_hashes(tmp_path) == source


def test_documented_restore_preserves_original_and_supports_rollback(demo, client, tmp_path, tmp_path_factory):
    # All paths belong to pytest; no real workspace or running app is used.
    scratch = tmp_path_factory.mktemp("restore")
    output = scratch / "backups"
    queued = client.post(f"/api/pipelines/{demo['pipeline_id']}/run").json()
    executed = worker.execute_run(queued["id"])
    assert executed["status"] == "completed"
    artifact = executed["artifact_id"]
    download = client.get(f"/api/artifacts/{artifact}/download").content
    source = workspace_hashes(tmp_path)
    assert run("--workspace", tmp_path, "--output", output).returncode == 0
    assert workspace_hashes(tmp_path) == source
    with tarfile.open(next(output.glob("*.tar.gz"))) as tar:
        manifest = json.load(tar.extractfile("dataguard-workspace/manifest.json"))
        # sqlite3.backup() may change database header bytes while preserving rows.
        snapshot = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
        tar.extractall(scratch, filter="data")

    # A newer project must survive in the preserved original and on rollback.
    newer = client.post("/api/projects", json={"name": "Keep this newer project"}).json()["id"]
    original = workspace_hashes(tmp_path)
    variables = {"restore_workspace": tmp_path, "restore_scratch": scratch}
    checked = restore_command("# Validate the extracted snapshot", **variables)
    assert checked.returncode == 0, checked.stderr
    assert workspace_hashes(tmp_path) == original
    switched = restore_command("# Preserve the original workspace", **variables)
    assert switched.returncode == 0, switched.stderr
    previous = next(tmp_path.parent.glob(tmp_path.name + ".before-restore-*"))
    assert workspace_hashes(previous) == original
    assert workspace_hashes(tmp_path) == snapshot

    # The app can resolve the original absolute paths and read saved data/output.
    projects = client.get("/api/projects").json()
    assert demo["project_id"] in {project["id"] for project in projects}
    assert newer not in {project["id"] for project in projects}
    assert client.get(f"/api/datasets/{demo['dataset_ids'][0]}/rows").json()["rows"]
    assert client.get(f"/api/pipelines/{demo['pipeline_id']}").json()["current_version"] == 1
    assert client.get(f"/api/artifacts/{artifact}/download").content == download
    repeated = client.post(f"/api/pipelines/{demo['pipeline_id']}/run").json()
    assert worker.execute_run(repeated["id"])["status"] == "completed"

    rolled_back = restore_command("# Roll back after stopping", **variables, restore_previous=previous)
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert workspace_hashes(tmp_path) == original
    assert newer in {project["id"] for project in client.get("/api/projects").json()}
    assert next(tmp_path.parent.glob(tmp_path.name + ".rejected-*")).is_dir()


def test_documented_restore_rejects_corruption_without_moving_original(demo, tmp_path):
    output = tmp_path / "backups"
    assert run("--workspace", tmp_path, "--output", output).returncode == 0
    scratch = tmp_path / "restore"
    scratch.mkdir()
    with tarfile.open(next(output.glob("*.tar.gz"))) as tar:
        tar.extractall(scratch, filter="data")
    before = workspace_hashes(tmp_path)
    next((scratch / "dataguard-workspace/raw").iterdir()).write_bytes(b"corrupted extracted file")
    result = restore_command("# Validate the extracted snapshot", restore_workspace=tmp_path,
                             restore_scratch=scratch)
    assert result.returncode != 0
    assert "File verification failed" in result.stderr
    assert workspace_hashes(tmp_path) == before
    assert not list(tmp_path.parent.glob(tmp_path.name + ".before-restore-*"))
