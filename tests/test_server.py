"""
tests/test_server.py
ChatGPT Connector API サーバーのテスト
"""
import pytest

from ugh_audit.scorer.ugh_scorer import UGHScorer
from ugh_audit.storage.audit_db import AuditDB

# FastAPI / httpx はオプショナル依存 — なければスキップ
fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from ugh_audit.server import app, configure  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    return AuditDB(db_path=tmp_path / "test_audit.db")


@pytest.fixture
def client(tmp_db):
    """テスト用クライアント（tmp_path DB を使用）"""
    scorer = UGHScorer(model_id="test-server")
    configure(db=tmp_db, scorer=scorer)
    with TestClient(app) as c:
        yield c
    # リセット
    configure(db=None, scorer=None)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_audit_returns_scores(client):
    resp = client.post("/api/audit", json={
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
        "reference": "AIは意味と共振する動的プロセスです。",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "por" in data
    assert "delta_e" in data
    assert "grv" in data
    assert "verdict" in data
    assert "saved_id" in data
    assert isinstance(data["saved_id"], int)
    assert data["saved_id"] >= 1


def test_audit_saves_to_db(client, tmp_db):
    client.post("/api/audit", json={
        "question": "テスト質問",
        "response": "テスト回答",
    })
    rows = tmp_db.list_recent(10)
    assert len(rows) == 1
    assert rows[0]["question"] == "テスト質問"


def test_audit_without_reference(client):
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    assert resp.status_code == 200
    assert "verdict" in resp.json()


def test_history_empty(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_returns_items(client):
    # 2件監査を実行
    for i in range(2):
        client.post("/api/audit", json={
            "question": f"質問{i}",
            "response": f"回答{i}",
        })
    resp = client.get("/api/history?limit=10")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert "id" in items[0]
    assert "por" in items[0]
    assert "meaning_drift" in items[0]


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/audit" in schema["paths"]
    assert "/api/history" in schema["paths"]


def test_ai_plugin_manifest(client):
    resp = client.get("/.well-known/ai-plugin.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name_for_model"] == "ugh_audit"
    assert data["api"]["type"] == "openapi"
