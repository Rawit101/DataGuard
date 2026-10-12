"""Serve the built frontend and API on one loopback port."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "frontend/dist/index.html").is_file():
    raise SystemExit("Build the frontend first: cd frontend && npm run build")
python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
os.chdir(ROOT)
os.execv(str(python if python.exists() else sys.executable),
         [str(python if python.exists() else sys.executable), "-m", "uvicorn", "backend.app.main:app",
          "--host", "127.0.0.1", "--port", "8000"])
