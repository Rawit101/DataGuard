import pytest
from fastapi.testclient import TestClient

from backend.app import config, db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(db, "WORKSPACE", tmp_path)
    monkeypatch.setattr(main, "WORKSPACE", tmp_path)
    monkeypatch.setenv("DATAGUARD_NO_WORKER", "1")
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def demo(client):
    result = client.post("/api/demo/load")
    assert result.status_code == 200, result.text
    return result.json()
