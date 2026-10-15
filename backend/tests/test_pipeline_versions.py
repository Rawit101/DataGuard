import json
import threading
from concurrent.futures import ThreadPoolExecutor

import duckdb

from backend.app import config, db, main, worker
from backend.tests.test_full_system import commit, inspect, project


def fixture(client):
    p = project(client, 'Five independent pipelines')
    stage = inspect(client, 'source.csv', b'id,amount\n1,10\n2,20\n')
    ds = commit(client, p['id'], stage.json(), 'source').json()
    return p['id'], ds['id']


def definition(source, value):
    return {'source_dataset_id': source, 'pre_rule_ids': [], 'post_rule_ids': [], 'strict_quality': True,
            'output': {'format': 'csv', 'filename': 'result.csv'}, 'steps': [
                {'type': 'cast', 'column': 'amount', 'target_type': 'DOUBLE', 'failure_policy': 'error'},
                {'type': 'calculate', 'as': 'computed', 'expression': {
                    'op': 'add', 'left': {'column': 'amount'}, 'right': {'value': value}}}]}


def test_five_pipeline_ids_versions_and_saved_execution(client):
    p, source = fixture(client)
    saved = []
    for index in range(1, 6):
        response = client.post('/api/pipelines', json={'project_id': p, 'name': f'pipeline_test_{index:02}',
                                                     'definition': definition(source, index)})
        assert response.status_code == 200, response.text
        saved.append(response.json()['id'])
    assert len(set(saved)) == 5
    before = {i: client.get(f'/api/pipelines/{i}').json() for i in saved}
    response = client.put(f'/api/pipelines/{saved[0]}', json={'definition': definition(source, 100),
                                                           'expected_current_version': 1})
    assert response.json()['version'] == 2
    assert client.get(f'/api/pipelines/{saved[0]}?version=1').json()['definition'] == definition(source, 1)
    assert client.get(f'/api/pipelines/{saved[0]}?version=2').json()['definition'] == definition(source, 100)
    for pipeline in saved[1:]:
        assert client.get(f'/api/pipelines/{pipeline}').json() == before[pipeline]
    db.initialize_db()
    assert len(client.get(f'/api/projects/{p}/pipelines').json()) == 5
    for index, pipeline in enumerate(saved, 1):
        version = 2 if index == 1 else 1
        queued = client.post(f'/api/pipelines/{pipeline}/run?version={version}').json()
        result = worker.execute_run(queued['id'])
        assert result['status'] == 'completed'
        with duckdb.connect() as con:
            values = con.execute('SELECT computed FROM read_csv(?) ORDER BY computed',
                                 [str(config.WORKSPACE / 'exports' / (result['artifact_id'] + '.csv'))]).fetchall()
        extra = 100 if index == 1 else index
        assert values == [(10 + extra,), (20 + extra,)]
    assert client.get(f'/api/pipelines/{saved[0]}?version=99').status_code == 404
    assert client.get(f'/api/pipelines/{saved[0]}?version=0').status_code == 400


def test_duplicate_names_ids_constraints_and_historical_duplicate(client):
    p, source = fixture(client)
    payload = {'id': 'a' * 32, 'project_id': p, 'name': 'Same name', 'definition': definition(source, 1)}
    first = client.post('/api/pipelines', json=payload)
    assert first.status_code == 200
    assert client.post('/api/pipelines', json=payload).status_code == 409
    second = client.post('/api/pipelines', json={**payload, 'id': 'b' * 32})
    assert second.status_code == 200
    client.put('/api/pipelines/' + 'a' * 32, json={'definition': definition(source, 99)})
    copied = client.post('/api/pipelines/' + 'a' * 32 + '/duplicate?version=1').json()
    assert copied['id'] not in {'a' * 32, 'b' * 32}
    assert client.get(f"/api/pipelines/{copied['id']}").json()['definition'] == definition(source, 1)
    assert len(client.get(f'/api/projects/{p}/pipelines').json()) == 3


def test_concurrent_expected_version_rejects_stale_writer_without_losing_history(client, monkeypatch):
    p, source = fixture(client)
    pipeline = client.post('/api/pipelines', json={'project_id': p, 'name': 'Concurrent',
                                                  'definition': definition(source, 1)}).json()['id']
    barrier = threading.Barrier(2)
    original = main.validate_definition
    def synchronize(*args):
        result = original(*args); barrier.wait(timeout=5); return result
    monkeypatch.setattr(main, 'validate_definition', synchronize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda value: client.put(f'/api/pipelines/{pipeline}', json={
            'definition': definition(source, value), 'expected_current_version': 1}), [2, 3]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with db.connect() as connection:
        versions = connection.execute('SELECT version,definition_json FROM pipeline_versions WHERE pipeline_id=? ORDER BY version', (pipeline,)).fetchall()
    assert [v[0] for v in versions] == [1, 2]
    assert json.loads(versions[0][1]) == definition(source, 1)


def test_quality_severity_is_independent_from_result_and_execution_errors(client):
    p, source = fixture(client)
    required = client.post('/api/quality-rules', json={'project_id': p, 'dataset_id': source,
        'name': 'Error severity can pass', 'kind': 'required', 'severity': 'error', 'config': {'column': 'id'}}).json()
    result = client.post(f"/api/quality-rules/{required['id']}/run").json()
    assert result['severity'] == 'error' and result['status'] == 'passed' and result['failed_rows'] == 0
    with db.connect() as connection:
        connection.execute('UPDATE datasets SET data_path=? WHERE id=?', (str(config.WORKSPACE / 'missing.parquet'), source))
    response = client.post(f"/api/quality-rules/{required['id']}/run")
    assert response.status_code == 400
    history = client.get(f"/api/quality-rules/{required['id']}/runs").json()
    assert len(history) == 1 and history[0]['status'] == 'passed'
