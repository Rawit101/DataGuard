"""Build and launch the local UI, API, and worker at one browser URL.

This serves a production build (no hot reload). For live UI reloading, run the API
on port 8000 and `npm run dev` in frontend/, then open http://127.0.0.1:5173.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_bin() -> Path:
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return candidate if candidate.exists() else Path(sys.executable)


def check_port(port: int) -> None:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"Port {port} is busy. Close the other app or free the port and retry.") from exc


def main() -> None:
    check_port(8000)
    if not (ROOT / "frontend/node_modules").exists():
        raise SystemExit("Install frontend dependencies first: cd frontend && npm ci")
    subprocess.run(["npm.cmd" if os.name == "nt" else "npm", "run", "build"],
                   cwd=ROOT / "frontend", check=True)
    backend = subprocess.Popen([str(python_bin()), "-m", "uvicorn", "backend.app.main:app",
                                "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT)
    print("DataGuard: http://127.0.0.1:8000", flush=True)
    try:
        while backend.poll() is None:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if backend.poll() is None:
            backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait()


if __name__ == "__main__":
    main()
