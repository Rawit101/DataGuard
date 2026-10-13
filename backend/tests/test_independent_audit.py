"""Regression checks from the independent review of the public repository."""
import duckdb

from backend.app import config
from backend.tests.test_full_system import commit, inspect, project


def test_import_reports_integer_overflow_as_a_parse_issue(client):
    p = project(client)
    stage = inspect(client, 'overflow.csv', b'quantity\n9223372036854775808\n7\n')
    result = commit(client, p['id'], stage.json(), 'overflow', {'quantity': 'BIGINT'})
    assert result.status_code == 200, result.text
    assert result.json()['issue_count'] == 1
    issues = client.get(f"/api/datasets/{result.json()['id']}/issues").json()
    assert issues['rows'][0]['raw_line'] == '9223372036854775808'


def test_nonfinite_sql_values_are_represented_without_failing_json(client):
    p = project(client)
    stage = inspect(client, 'one.csv', b'id\n1\n')
    assert commit(client, p['id'], stage.json(), 'one').status_code == 200
    result = client.post('/api/queries/run', json={
        'project_id': p['id'],
        'sql': "SELECT CAST('NaN' AS DOUBLE) AS missing, CAST('Infinity' AS DOUBLE) AS positive, CAST('-Infinity' AS DOUBLE) AS negative FROM one"
    })
    assert result.status_code == 200, result.text
    assert result.json()['rows'] == [{'missing': 'NaN', 'positive': 'Infinity', 'negative': '-Infinity'}]


def test_nonfinite_parquet_profile_and_rows_are_json_safe(client):
    p = project(client)
    path = config.WORKSPACE / 'nonfinite.parquet'
    with duckdb.connect() as con:
        con.execute("COPY (SELECT CAST('Infinity' AS DOUBLE) AS amount) TO ? (FORMAT parquet)", [str(path)])
    stage = inspect(client, path.name, path.read_bytes())
    result = commit(client, p['id'], stage.json(), 'nonfinite')
    assert result.status_code == 200, result.text
    dataset = result.json()['id']
    rows = client.get(f'/api/datasets/{dataset}/rows')
    assert rows.status_code == 200, rows.text
    assert rows.json()['rows'][0]['amount'] == 'Infinity'
    profile = client.get(f'/api/datasets/{dataset}/profile')
    assert profile.status_code == 200, profile.text
    assert profile.json()['columns'][0]['mean'] == 'Infinity'


def test_pipeline_internal_ctes_do_not_shadow_dataset_aliases(client):
    p = project(client)
    stage = inspect(client, 'step_0.csv', b'id\nA\n')
    ds = commit(client, p['id'], stage.json(), 'step_0').json()
    response = client.post('/api/pipelines/preview', json={
        'project_id': p['id'], 'name': 'Collision', 'definition': {
            'source_dataset_id': ds['id'], 'steps': [], 'pre_rule_ids': [], 'post_rule_ids': [],
            'strict_quality': False, 'output': {'format': 'csv', 'filename': 'out.csv'}
        }
    })
    assert response.status_code == 200, response.text
    assert response.json()['rows'][0]['id'] == 'A'


def test_deduplicate_does_not_filter_by_user_rank_column(client):
    p = project(client)
    stage = inspect(client, 'ranks.csv', b'id,__rank\nA,2\nA,3\nB,4\n')
    ds = commit(client, p['id'], stage.json(), 'ranks').json()
    response = client.post('/api/pipelines/preview', json={
        'project_id': p['id'], 'name': 'Deduplicate', 'definition': {
            'source_dataset_id': ds['id'], 'steps': [{'type': 'deduplicate', 'keys': ['id']}],
            'pre_rule_ids': [], 'post_rule_ids': [], 'strict_quality': False,
            'output': {'format': 'csv', 'filename': 'out.csv'}
        }
    })
    assert response.status_code == 200, response.text
    assert {r['id']: r['__rank'] for r in response.json()['rows']} == {'A': '2', 'B': '4'}


def test_unique_rule_keeps_user_duplicate_count_column_in_failure_export(client):
    p = project(client)
    stage = inspect(client, 'duplicates.csv', b'id,__duplicate_count\nA,10\nA,20\nB,30\n')
    ds = commit(client, p['id'], stage.json(), 'duplicates').json()
    rule = client.post('/api/quality-rules', json={
        'project_id': p['id'], 'dataset_id': ds['id'], 'name': 'Unique IDs',
        'kind': 'unique', 'severity': 'warning', 'config': {'columns': ['id']}
    }).json()
    result = client.post(f"/api/quality-rules/{rule['id']}/run").json()
    assert result['failed_rows'] == 2
    assert [r['__duplicate_count'] for r in result['examples']] == ['10', '20']
    exported = client.get(f"/api/artifacts/{result['artifact_id']}/download")
    assert '__duplicate_count' in exported.text.splitlines()[0]


def test_bulk_csv_import_keeps_multiline_quotes_empty_text_nulls_and_source_ids(client):
    p = project(client)
    raw = b'id,label\n1,"comma,\"\"quote\"\""\n2,"line\nnext"\nwrong,field,count\n3,\n4,NA\n'
    stage = inspect(client, 'special.csv', raw, '{"null_tokens":["NA"]}')
    ds = commit(client, p['id'], stage.json(), 'special').json()
    result = client.get(f"/api/datasets/{ds['id']}/rows").json()
    assert [(r['__source_row_id'], r['label']) for r in result['rows']] == [
        (1, 'comma,"quote"'), (2, 'line\nnext'), (4, ''), (5, None)]
    assert ds['issue_count'] == 1
    assert not list(config.WORKSPACE.glob('tmp/*.accepted.csv'))


def test_bulk_csv_import_handles_header_only_file(client):
    p = project(client)
    stage = inspect(client, 'empty_rows.csv', b'id,label\n')
    response = commit(client, p['id'], stage.json(), 'empty_rows')
    assert response.status_code == 200, response.text
    assert response.json()['row_count'] == 0
    assert client.get(f"/api/datasets/{response.json()['id']}/rows").json()['rows'] == []
