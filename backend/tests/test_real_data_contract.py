import pytest

from backend.tests.test_full_system import commit, inspect, project


@pytest.mark.parametrize('mode,expected', [('all', 2), ('beyond_first', 1)])
def test_unique_count_policy_matches_failure_examples_and_export(client, mode, expected):
    p = project(client)
    raw = b'customer_id,name\n4,Mali\n4,Mali\n5,Nida\n,Unknown\n,Unknown\n'
    ds = commit(client, p['id'], inspect(client, 'customers.csv', raw).json(), 'customers').json()
    rule = client.post('/api/quality-rules', json={'project_id': p['id'], 'dataset_id': ds['id'],
        'name': 'Duplicates', 'kind': 'unique', 'config': {'columns': ['customer_id'], 'count_mode': mode}})
    assert rule.status_code == 200, rule.text
    result = client.post(f"/api/quality-rules/{rule.json()['id']}/run").json()
    assert result['evaluated_rows'] == 5
    assert result['failed_rows'] == expected
    assert len(result['examples']) == expected
    assert all(row['customer_id'] == '4' for row in result['examples'])
    output = client.get(f"/api/artifacts/{result['artifact_id']}/download").text
    assert len(output.strip().splitlines()) == expected + 1


@pytest.mark.parametrize('mode', ['typo', []])
def test_unknown_unique_count_policy_is_rejected(client, mode):
    p = project(client)
    ds = commit(client, p['id'], inspect(client, 'a.csv', b'id\n1\n').json(), 'items').json()
    result = client.post('/api/quality-rules', json={'project_id': p['id'], 'dataset_id': ds['id'],
        'name': 'Invalid', 'kind': 'unique', 'config': {'columns': ['id'], 'count_mode': mode}})
    assert result.status_code == 400


def test_invalid_encoding_explains_decode_error_instead_of_empty_csv(client):
    result = inspect(client, 'invalid_encoding.csv', b'id,label\n1,\xff\xfe\n')
    assert result.status_code == 400
    assert 'utf-8' in result.text
    assert 'decode' in result.text
