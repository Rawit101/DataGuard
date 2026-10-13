"""Reproducible isolated CSV load + real-worker + process-restart verification.

Run from the repository root using .venv/bin/python. Uses port 8001 and a
new temporary workspace; leaves user's normal workspace unchanged.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

import duckdb
import httpx


ROOT = Path(__file__).resolve().parents[2]
BASE = 'http://127.0.0.1:8001'


def main():
    row_count = int(os.environ.get('DATAGUARD_AUDIT_ROWS', '1000000'))
    if not 1 <= row_count <= 1_000_000:
        raise SystemExit('Choose 1..1,000,000 rows for this bounded local audit')
    with tempfile.TemporaryDirectory(prefix='dataguard-independent-') as directory:
        work = Path(directory)
        source = work / 'independent_sales.csv'
        expected = {}
        with source.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['id', 'region', 'amount', 'description'])
            for i in range(row_count):
                region, amount = f'region_{i % 10}', i % 1000 + 1
                writer.writerow([i, region, amount, 'X' * 80 + str(i)])
                group = expected.setdefault(region, {'rows': 0, 'total': 0})
                group['rows'] += 1
                group['total'] += amount
        report = {'input_rows': row_count, 'input_bytes': source.stat().st_size}
        app_workspace = work / 'app'
        env = {**os.environ, 'DATAGUARD_WORKSPACE': str(app_workspace)}
        process = None
        stop_monitor = threading.Event()
        peak_rss = 0

        def memory_monitor():
            nonlocal peak_rss
            while not stop_monitor.wait(0.05):
                if process is None:
                    continue
                pids = [process.pid]
                children = Path(f'/proc/{process.pid}/task/{process.pid}/children')
                try:
                    pids += [int(pid) for pid in children.read_text().split()]
                    total = 0
                    for pid in pids:
                        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                            if line.startswith('VmRSS:'):
                                total += int(line.split()[1]) * 1024
                    peak_rss = max(peak_rss, total)
                except OSError:
                    pass

        monitor = threading.Thread(target=memory_monitor, daemon=True)
        monitor.start()
        with (work / 'server.log').open('w') as logs, httpx.Client(base_url=BASE, timeout=180, trust_env=False) as client:
            def start():
                nonlocal process
                process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:app',
                    '--host', '127.0.0.1', '--port', '8001'], cwd=ROOT, env=env, stdout=logs, stderr=logs)
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Server failed to start')
                    try:
                        if client.get('/api/health').status_code == 200:
                            return
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                raise RuntimeError('Server health timed out')

            def check(response):
                response.raise_for_status()
                return response.json()

            def post(path, payload):
                return check(client.post(path, json=payload))

            try:
                start()
                project = post('/api/projects', {'name': 'Independent large CSV audit'})['id']
                started = time.monotonic()
                with source.open('rb') as handle:
                    stage = check(client.post('/api/imports/inspect', files={'file': (source.name, handle)},
                        data={'options': '{}'}))
                report['inspect_seconds'] = round(time.monotonic() - started, 3)
                assert stage['preview']['row_count'] == row_count
                started = time.monotonic()
                dataset = post('/api/imports/commit', {'project_id': project, 'token': stage['token'],
                    'name': 'Independent sales', 'alias': 'independent_sales', 'type_overrides': {}})['id']
                report['commit_seconds'] = round(time.monotonic() - started, 3)
                started = time.monotonic()
                page = check(client.get(f'/api/datasets/{dataset}/rows', params={
                    'page': max(1, (row_count + 49) // 50), 'page_size': 50}))
                assert page['total_rows'] == row_count and len(page['rows']) <= 50
                report['last_page_seconds'] = round(time.monotonic() - started, 3)
                started = time.monotonic()
                query = post('/api/queries/run', {'project_id': project, 'sql':
                    'SELECT region,COUNT(*) AS n,SUM(CAST(amount AS BIGINT)) AS total FROM independent_sales GROUP BY region'})
                actual = {r['region']: {'rows': r['n'], 'total': r['total']} for r in query['rows']}
                assert actual == expected
                report['sql_seconds'] = round(time.monotonic() - started, 3)
                pipeline = post('/api/pipelines', {'project_id': project, 'name': 'Actual CSV sums', 'definition': {
                    'source_dataset_id': dataset, 'steps': [
                        {'type': 'cast', 'column': 'amount', 'target_type': 'BIGINT', 'failure_policy': 'error'},
                        {'type': 'group', 'group_by': ['region'], 'aggregations': [
                            {'function': 'count', 'as': 'n'}, {'function': 'sum', 'column': 'amount', 'as': 'total'}]}
                    ], 'pre_rule_ids': [], 'post_rule_ids': [], 'strict_quality': True,
                    'output': {'format': 'parquet', 'filename': 'independent_summary.parquet'}
                }})['id']
                started = time.monotonic()
                run = post(f'/api/pipelines/{pipeline}/run', {})['id']
                deadline = time.monotonic() + 120
                while True:
                    state = check(client.get(f'/api/runs/{run}'))
                    if state['status'] in {'completed', 'failed', 'cancelled'}:
                        assert state['status'] == 'completed', state
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Pipeline timed out')
                    time.sleep(0.1)
                report['pipeline_seconds'] = round(time.monotonic() - started, 3)
                artifact = state['output_artifact_id']
                downloaded = client.get(f'/api/artifacts/{artifact}/download')
                downloaded.raise_for_status()
                output = work / 'readback.parquet'
                output.write_bytes(downloaded.content)
                with duckdb.connect() as con:
                    rows = con.execute('SELECT region,n,total FROM read_parquet(?)', [str(output)]).fetchall()
                assert {r[0]: {'rows': r[1], 'total': r[2]} for r in rows} == expected
                report['output_rows'] = len(rows)
                report['output_bytes'] = len(downloaded.content)
                process.terminate()
                process.wait(timeout=10)
                start()
                assert check(client.get(f'/api/runs/{run}'))['status'] == 'completed'
                assert len(check(client.get(f'/api/projects/{project}/datasets'))) == 1
                assert client.get(f'/api/artifacts/{artifact}/download').content == downloaded.content
                report['restart_persistence'] = 'PASS'
                logs.flush()
                assert 'Traceback' not in (work / 'server.log').read_text()
                report['combined_api_worker_peak_rss_bytes'] = peak_rss if peak_rss else None
                print(json.dumps(report, indent=2))
            finally:
                stop_monitor.set()
                monitor.join(timeout=2)
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    main()
