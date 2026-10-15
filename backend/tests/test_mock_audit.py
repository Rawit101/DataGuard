"""Real-data regressions found during the repository-wide mock/functionality audit."""
import csv
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest

from backend.app import config, db, main
from backend.tests.test_full_system import commit, inspect, project


def imported(client, contents=b'id,label\nA,one\nB,two\n', overrides=None):
    p = project(client)
    stage = inspect(client, 'audit.csv', contents)
    response = commit(client, p['id'], stage.json(), 'audit', overrides)
    assert response.status_code == 200, response.text
    return p['id'], response.json()


def plan(dataset, **changes):
    return {'source_dataset_id': dataset, 'steps': [], 'pre_rule_ids': [],
            'post_rule_ids': [], 'strict_quality': False,
            'output': {'format': 'csv', 'filename': 'audit.csv'}, **changes}


@pytest.mark.parametrize('changes', [
    {'output': {'format': 'xlsx', 'filename': 'bad.xlsx'}},
    {'output': {'format': 'csv', 'filename': '../bad.csv'}},
    {'output': 'csv'},
    {'pre_rule_ids': 'not-a-list'},
    {'steps': [{'type': 'rename', 'mapping': []}]},
    {'steps': [{'type': 'select', 'columns': 'id'}]},
    {'steps': [{'type': 'calculate', 'as': 'extra', 'expression': {'op': 'add'}}]},
    {'steps': [{'type': 'rename', 'mapping': {'label': 'ID'}}]},
    {'steps': [{'type': 'select', 'columns': ['id', 'id']}]},
    {'steps': [{'type': 'group', 'aggregations': [{'function': [], 'as': 'count'}]}]},
    {'steps': [{'type': 'calculate', 'as': 'extra', 'expression': {'op': []}}]},
    {'steps': [{'type': 'sort', 'by': [{}]}]},
])
def test_invalid_pipeline_is_rejected_before_saving(client, changes):
    p, ds = imported(client)
    response = client.post('/api/pipelines', json={
        'project_id': p, 'name': 'Audit invalid', 'definition': plan(ds['id'], **changes)})
    assert response.status_code == 400, response.text
    assert client.get(f'/api/projects/{p}/pipelines').json() == []


def test_native_parquet_numeric_profile_and_override_issues(client):
    p = project(client)
    path = config.WORKSPACE / 'audit-native.parquet'
    with duckdb.connect() as con:
        con.execute("COPY (SELECT x::INTEGER AS quantity, x::DECIMAL(8,2) AS price, "
                    "CASE WHEN x=1 THEN 'bad' ELSE '2' END AS raw_amount FROM range(1,3) t(x)) "
                    "TO ? (FORMAT parquet)", [str(path)])
    stage = inspect(client, path.name, path.read_bytes())
    ds = commit(client, p['id'], stage.json(), 'native', {'raw_amount': 'DOUBLE'}).json()
    columns = {c['name']: c for c in client.get(f"/api/datasets/{ds['id']}/profile").json()['columns']}
    assert columns['quantity']['mean'] == 1.5
    assert columns['price']['mean'] == 1.5
    assert columns['raw_amount']['invalid_count'] == 1
    assert ds['issue_count'] == 1
    assert client.get(f"/api/datasets/{ds['id']}/issues").json()['rows'][0]['raw_line'] == 'bad'


def test_quality_export_preserves_columns_named_like_report_metadata(client):
    p, ds = imported(client, b'id,rule_name,reason\nA,original-one,user-one\nA,original-two,user-two\n')
    rule = client.post('/api/quality-rules', json={
        'project_id': p, 'dataset_id': ds['id'], 'name': 'Duplicate check',
        'kind': 'unique', 'severity': 'error', 'config': {'columns': ['id']}}).json()
    result = client.post(f"/api/quality-rules/{rule['id']}/run").json()
    download = client.get(f"/api/artifacts/{result['artifact_id']}/download")
    reader = csv.DictReader(io.StringIO(download.text))
    assert len(reader.fieldnames) == len(set(reader.fieldnames))
    rows = list(reader)
    assert [row['rule_name'] for row in rows] == ['original-one', 'original-two']
    assert [row['reason'] for row in rows] == ['user-one', 'user-two']


def test_parse_issue_uses_physical_line_after_multiline_csv_record(client):
    _, ds = imported(client, b'id,label,amount\nA,"two\nlines",2\nB,plain,bad\n', {'amount': 'DOUBLE'})
    issue = client.get(f"/api/datasets/{ds['id']}/issues").json()['rows'][0]
    assert issue['line'] == 4
    assert issue['raw_line'] == 'bad'


@pytest.mark.parametrize('config_changes', [
    {'column': 'id', 'type': 12},
    {'columns': 'id'},
])
def test_bad_rule_configuration_returns_validation_error(client, config_changes):
    p, ds = imported(client)
    response = client.post('/api/quality-rules', json={
        'project_id': p, 'dataset_id': ds['id'], 'name': 'Invalid rule',
        'kind': 'parse_valid', 'severity': 'error', 'config': config_changes})
    assert response.status_code == 400, response.text


def test_quality_history_preserves_execution_rule_snapshot_after_edit(client):
    p, ds = imported(client, b'id\nA\nA\nB\n')
    payload = {'project_id': p, 'dataset_id': ds['id'], 'name': 'Original unique check',
               'kind': 'unique', 'severity': 'warning', 'config': {'columns': ['id']}}
    rule = client.post('/api/quality-rules', json=payload).json()
    executed = client.post(f"/api/quality-rules/{rule['id']}/run").json()
    assert executed['failed_rows'] == 2
    changed = {**payload, 'name': 'Changed required check', 'kind': 'required',
               'config': {'column': 'id'}}
    assert client.put(f"/api/quality-rules/{rule['id']}", json=changed).status_code == 200
    history = client.get(f"/api/quality-rules/{rule['id']}/runs").json()
    assert history[0]['rule_snapshot'] == {
        'name': 'Original unique check', 'kind': 'unique', 'severity': 'warning',
        'dataset_id': ds['id'], 'config': {'columns': ['id']}}
    assert history[0]['examples'] == executed['examples']
    assert history[0]['artifact_id'] == executed['artifact_id']


def test_schema_two_upgrade_preserves_existing_quality_history(client):
    p, ds = imported(client)
    rule = client.post('/api/quality-rules', json={
        'project_id': p, 'dataset_id': ds['id'], 'name': 'Legacy', 'kind': 'required',
        'severity': 'error', 'config': {'column': 'id'}}).json()
    result = client.post(f"/api/quality-rules/{rule['id']}/run").json()
    with db.connect() as con:
        con.execute('ALTER TABLE quality_runs DROP COLUMN rule_snapshot_json')
        con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    db.initialize_db()
    entry = client.get(f"/api/quality-rules/{rule['id']}/runs").json()[0]
    assert entry['id'] == result['id']
    assert entry['evaluated_rows'] == 2
    assert entry['rule_snapshot'] is None
    assert client.get('/api/settings').json()['schema_version'] == 3


def test_decimal_overflow_is_reported_without_losing_raw_value(client):
    _, ds = imported(client, b'amount\n1000000000000000\n1.25\n', {'amount': 'DECIMAL'})
    assert ds['issue_count'] == 1
    rows = client.get(f"/api/datasets/{ds['id']}/rows").json()['rows']
    assert rows[0]['amount'] == '1000000000000000'
    assert client.get(f"/api/datasets/{ds['id']}/profile").json()['columns'][0]['invalid_count'] == 1


def test_concurrent_pipeline_saves_allocate_distinct_versions(client, monkeypatch):
    p, ds = imported(client)
    definition = plan(ds['id'])
    pipeline = client.post('/api/pipelines', json={
        'project_id': p, 'name': 'Concurrent saves', 'definition': definition}).json()
    barrier = threading.Barrier(2)
    validate = main.validate_definition
    def synchronized_validation(*args):
        result = validate(*args)
        barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(main, 'validate_definition', synchronized_validation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.put(f"/api/pipelines/{pipeline['id']}",
                                                      json={'definition': definition}), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert {response.json()['version'] for response in responses} == {2, 3}
    assert len(client.get(f"/api/pipelines/{pipeline['id']}").json()['versions']) == 3


def test_deleting_a_referenced_rule_preserves_saved_pipeline(client):
    p, ds = imported(client)
    rule = client.post('/api/quality-rules', json={'project_id': p, 'dataset_id': ds['id'],
        'name': 'Saved gate', 'kind': 'required', 'config': {'column': 'id'}, 'severity': 'error'}).json()
    response = client.post('/api/pipelines', json={'project_id': p, 'name': 'Protected gate',
        'definition': plan(ds['id'], pre_rule_ids=[rule['id']])})
    assert response.status_code == 200, response.text
    deleted = client.delete(f"/api/quality-rules/{rule['id']}")
    assert deleted.status_code == 400
    assert 'saved pipeline version' in deleted.text
    assert client.get(f'/api/projects/{p}/quality-rules').json()[0]['id'] == rule['id']


@pytest.mark.parametrize('kind,cfg', [
    ('range', {'column': 'id', 'type': 'DOUBLE', 'min': 'invalid'}),
    ('range', {'column': 'id', 'type': 'DOUBLE', 'min': 10, 'max': 2}),
    ('regex', {'column': 'id', 'pattern': '(?=A)A'}),
])
def test_quality_engine_rejects_unexecutable_bounds_and_patterns(client, kind, cfg):
    p, ds = imported(client)
    response = client.post('/api/quality-rules', json={'project_id': p, 'dataset_id': ds['id'],
        'name': 'Invalid engine configuration', 'kind': kind, 'config': cfg, 'severity': 'error'})
    assert response.status_code == 400, response.text
    assert client.get(f'/api/projects/{p}/quality-rules').json() == []
