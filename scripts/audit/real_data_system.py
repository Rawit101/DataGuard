"""Real browser + SQLite/file assertions across a real isolated server restart.

Requires port 8000 free. Never connects to an already running application.
"""
from __future__ import annotations
import argparse
import hashlib
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


def snapshot(workspace):
    with sqlite3.connect(f'file:{workspace / "metadata.sqlite3"}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        records = {table: sorted([dict(r) for r in db.execute(f'SELECT * FROM "{table}"')],
                                key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}
    files = {str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
             for category in ('raw','data','exports') for p in (workspace/category).rglob('*') if p.is_file()}
    return {'records': records, 'files': files}


def assertions(workspace, state, evidence):
    records = snapshot(workspace)['records']
    p = state['project']
    assert any(r['id'] == p and 'renamed' in r['name'] for r in records['projects'])
    assert len([r for r in records['datasets'] if r['project_id']==p]) == 6
    assert not [r for r in records['datasets'] if r['project_id']==state['second']]
    datasets = {r['id']:r for r in records['datasets']}
    for filename, dataset_id in state['sources'].items():
        ds = datasets[dataset_id]
        fixture = ROOT/'tests/fixtures/e2e'/filename
        assert Path(ds['raw_path']).is_relative_to(workspace/'raw')
        assert Path(ds['data_path']).is_relative_to(workspace/'data')
        assert Path(ds['raw_path']).read_bytes() == fixture.read_bytes()
        assert ds['sha256'] == hashlib.sha256(fixture.read_bytes()).hexdigest()
        assert Path(ds['data_path']).is_file()
    runs = [r for r in records['runs'] if r['pipeline_id'] in {r['id'] for r in records['pipelines'] if r['project_id']==p}]
    assert len(runs)==4
    assert sum(r['status']=='failed' for r in runs)==2
    assert all(r['started_at'] and r['finished_at'] and r['duration_ms']>=0 for r in runs)
    assert not [a for a in records['artifacts'] if a['run_id'] in {r['id'] for r in runs if r['status']=='failed'}]
    assert len(state['overview']['runs']) == min(10,len(runs))
    assert len(state['overview']['datasets']) == 6
    assert len(state['overview']['pipelines']) == 4
    import duckdb
    with duckdb.connect() as con:
        actual = con.execute('SELECT COUNT(*), SUM(CAST(amount AS BIGINT)) FROM read_parquet(?)',
                             [datasets[state['sources']['large_dataset.csv']]['data_path']]).fetchone()
    assert actual == (50000, 24836625)
    result = {'status':'PASS','project_id':p,'stored_raw_bytes_and_sha256':'all six exact',
              'sqlite_project_dataset_run_artifact_relations':'PASS','blocked_runs_without_artifacts':2,
              'large_parquet_rows':actual[0],'large_parquet_sum':actual[1],
              'overview_counts':{k:len(state['overview'][k]) for k in ('datasets','pipelines','runs')}}
    (evidence/'database-assertions.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',choices=['chromium','webkit'],default='chromium')
    parser.add_argument('--evidence',type=Path,default=ROOT/'work/real-data-e2e-evidence/chromium')
    args=parser.parse_args()
    evidence=args.evidence.resolve();evidence.mkdir(parents=True,exist_ok=True)
    (evidence/'result.json').unlink(missing_ok=True)
    with socket.socket() as port:
        # Ignore TIME_WAIT from our just-stopped server; an active listener still fails.
        port.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        port.bind(('127.0.0.1',8000))
    workspace=Path(tempfile.mkdtemp(prefix=f'dataguard-real-{args.browser}-')).resolve()
    env={**os.environ,'DATAGUARD_WORKSPACE':str(workspace),'DATAGUARD_E2E_EXTERNAL_SERVER':'1',
         'DATAGUARD_EVIDENCE_DIR':str(evidence),'DATAGUARD_TEST_ENGINE':args.browser,
         'DATAGUARD_MAX_UPLOAD_BYTES':str(4*1024*1024),'DATAGUARD_QUERY_TIMEOUT_SECONDS':'2'}
    (evidence/'environment.json').write_text(json.dumps({k:env[k] for k in ('DATAGUARD_WORKSPACE','DATAGUARD_MAX_UPLOAD_BYTES','DATAGUARD_QUERY_TIMEOUT_SECONDS','DATAGUARD_TEST_ENGINE')},indent=2))
    process=None;log=None;pids=[]
    def start(label):
        nonlocal process,log
        log=(evidence/f'server-{label}.log').open('w')
        process=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/start.py')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        pids.append(process.pid)
        with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False) as c:
            for _ in range(200):
                if process.poll() is not None:raise RuntimeError('Server exited: '+(evidence/f'server-{label}.log').read_text()[-3000:])
                try:
                    if c.get('/api/health').is_success:
                        assert Path(c.get('/api/settings').json()['workspace']).resolve()==workspace
                        return
                except httpx.TransportError:pass
                time.sleep(0.1)
        raise RuntimeError('Server startup timeout')
    def stop():
        nonlocal process,log
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            process=None
        if log:log.close();log=None
    def browser(label,restart=False):
        command=['npm','run','e2e','--','--workers=1',f'--browser={args.browser}']
        if restart:command+=['--grep','after an actual server restart|survive process restart']
        with (evidence/f'browser-{label}.log').open('w') as output:
            result=subprocess.run(command,cwd=ROOT/'frontend',env={**env,'DATAGUARD_VERIFY_RESTART':'1' if restart else '0'},stdout=output,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError((evidence/f'browser-{label}.log').read_text()[-8000:])
    try:
        start('before-restart')
        with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False) as client:
            (evidence/'openapi.json').write_text(json.dumps(client.get('/openapi.json').json(),indent=2))
        browser('before-restart')
        state=json.loads((evidence/f'{args.browser}-real-data-state.json').read_text())
        checked=assertions(workspace,state,evidence)
        before=snapshot(workspace)
        (evidence/'database-before-restart.json').write_text(json.dumps(before,indent=2))
        stop();start('after-restart')
        after=snapshot(workspace)
        assert before==after,'Metadata records or stored file hashes changed across restart'
        (evidence/'database-after-restart.json').write_text(json.dumps(after,indent=2))
        browser('after-restart',True)
        with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False) as client:
            for a in state['artifacts']:
                response=client.get(f"/api/artifacts/{a['id']}/download")
                assert response.is_success
                row=next(r for r in before['records']['artifacts'] if r['id']==a['id'])
                assert hashlib.sha256(response.content).hexdigest()==before['files'][str(Path(row['path']).relative_to(workspace))]
        result={'status':'PASS','browser':args.browser,'workspace':str(workspace),'server_pids':pids,
                'actual_restart':True,'all_metadata_rows_and_stored_file_hashes_identical_after_restart':True,
                'download_bytes_match_pre_restart_artifact_hashes':True,'database_assertions':checked,
                'table_counts_before_restart':{t:len(rows) for t,rows in before['records'].items()}}
        (evidence/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    finally:stop()


if __name__=='__main__':main()
