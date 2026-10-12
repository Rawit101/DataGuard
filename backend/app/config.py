from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get("DATAGUARD_WORKSPACE", ROOT / ".dataguard")).expanduser().resolve()
MAX_UPLOAD_BYTES = int(os.environ.get("DATAGUARD_MAX_UPLOAD_BYTES", str(256 * 1024 * 1024)))
MAX_PREVIEW_ROWS = 100
QUERY_TIMEOUT_SECONDS = int(os.environ.get("DATAGUARD_QUERY_TIMEOUT_SECONDS", "30"))
MEMORY_LIMIT = os.environ.get("DATAGUARD_MEMORY_LIMIT", "1GB")


def initialize_workspace() -> None:
    for name in ("raw", "data", "exports", "tmp"):
        (WORKSPACE / name).mkdir(parents=True, exist_ok=True)


def managed_path(category: str, name: str) -> Path:
    if category not in {"raw", "data", "exports", "tmp"} or not name or Path(name).name != name:
        raise ValueError("Invalid managed path")
    path = (WORKSPACE / category / name).resolve()
    if not path.is_relative_to(WORKSPACE / category):
        raise ValueError("Path escapes workspace")
    return path
