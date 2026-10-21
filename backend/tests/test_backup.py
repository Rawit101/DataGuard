import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, check=False)


def test_backup_creates_verified_archive(demo, tmp_path):
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
    # The source workspace is read, never modified.
    assert (tmp_path / "metadata.sqlite3").is_file()


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
