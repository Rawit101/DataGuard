"""Run five-pipeline browser checks across a real server restart in a fresh workspace."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def snapshot(workspace: Path):
    with sqlite3.connect(workspace / 'metadata.sqlite3') as db:
        db.row_factory = sqlite3.Row
        return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY 1,2')]
                for table in ('pipelines', 'pipeline_versions')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, default=ROOT / 'work/pipeline-version-evidence')
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    with socket.socket() as port:
        port.bind(('127.0.0.1', 8000))  # Refuse to reuse a user's running application.
    workspace = Path(tempfile.mkdtemp(prefix='dataguard-five-pipelines-')).resolve()
    env = {**os.environ, 'DATAGUARD_WORKSPACE': str(workspace), 'DATAGUARD_E2E_EXTERNAL_SERVER': '1',
           'DATAGUARD_EVIDENCE_DIR': str(evidence)}
    process = None
    log = None

    def start(label):
        nonlocal process, log
        log = (evidence / f'server-{label}.log').open('w')
        process = subprocess.Popen([str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/start.py')],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False) as client:
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError('Isolated server exited during startup')
                try:
                    if client.get('/api/health').is_success:
                        assert Path(client.get('/api/settings').json()['workspace']).resolve() == workspace
                        return
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
        raise RuntimeError('Isolated server did not become ready')

    def stop():
        nonlocal process, log
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            process = None
        if log is not None:
            log.close(); log = None

    def browser(label, pattern, restart=False):
        command = ['npm', 'run', 'e2e', '--', 'tests/pipeline-versions.spec.ts', '--workers=1', '--grep', pattern]
        with (evidence / f'browser-{label}.log').open('w') as output:
            result = subprocess.run(command, cwd=ROOT / 'frontend',
                                    env={**env, 'DATAGUARD_VERIFY_RESTART': '1' if restart else '0'},
                                    stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError((evidence / f'browser-{label}.log').read_text()[-7000:])

    try:
        start('before-restart')
        browser('before-restart', 'five independent|severity and')
        before = snapshot(workspace)
        selected = [p for p in before['pipelines'] if p['name'] in {f'pipeline_test_{i:02}' for i in range(1,6)}]
        assert len(selected) == 5 and len({p['id'] for p in selected}) == 5
        stop()
        start('after-restart')
        after = snapshot(workspace)
        assert before == after, 'Pipeline records or immutable versions changed across restart'
        browser('after-restart', 'after an actual server restart', restart=True)
        assert snapshot(workspace) == before, 'Running saved versions changed definitions'
        report = {'status': 'PASS', 'workspace': str(workspace), 'unique_pipeline_ids': [p['id'] for p in selected],
                  'pipeline_count': len(selected), 'actual_process_restart': True,
                  'definitions_unchanged_across_restart': True, 'all_five_executed_before_and_after_restart': True,
                  'severity_and_pass_fail_running_error_browser_checks': 'PASS', 'records': before}
        (evidence / 'restart-verification.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({key: value for key,value in report.items() if key != 'records'}, indent=2))
    finally:
        stop()


if __name__ == '__main__':
    main()
