"""
tests/test_server.py
ChatGPT Connector API サーバーのテスト
"""
import pytest

from ugh_audit.reference.golden_store import GoldenStore
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
def tmp_golden(tmp_path):
    return GoldenStore(path=tmp_path / "golden.json")


@pytest.fixture
def client(tmp_db, tmp_golden):
    """テスト用クライアント（tmp_path DB / GoldenStore を使用）"""
    scorer = UGHScorer(model_id="test-server")
    configure(db=tmp_db, scorer=scorer, golden=tmp_golden)
    with TestClient(app) as c:
        yield c
    # リセット
    configure(db=None, scorer=None, golden=None)


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


def test_audit_preserves_session_id(client, tmp_db):
    """session_id を指定すると同一値で DB に保存されることを検証"""
    for i in range(2):
        client.post("/api/audit", json={
            "question": f"質問{i}",
            "response": f"回答{i}",
            "session_id": "conv-abc",
        })
    rows = tmp_db.list_recent(10)
    assert len(rows) == 2
    assert all(r["session_id"] == "conv-abc" for r in rows)


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


def test_audit_resolves_reference_from_golden(tmp_db, tmp_path):
    """reference 省略時に GoldenStore から自動解決されることを検証"""
    golden = GoldenStore(path=tmp_path / "golden.json")
    # GoldenStore にはデフォルトで ugh_definition が入っている
    # "AIは意味を持てるか？" に対して find_reference が返る
    ref = golden.find_reference("AIは意味を持てるか？")
    assert ref is not None  # GoldenStore が解決できることを前提確認

    scorer = UGHScorer(model_id="test-server")
    configure(db=tmp_db, scorer=scorer, golden=golden)
    with TestClient(app) as c:
        resp = c.post("/api/audit", json={
            "question": "AIは意味を持てるか？",
            "response": "AIは意味を処理できます。",
            # reference を省略 → GoldenStore から自動解決される
        })
    configure(db=None, scorer=None, golden=None)
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_id"] >= 1

    # DB に保存された reference が question ではなく GoldenStore の値であることを確認
    rows = tmp_db.list_recent(1)
    assert rows[0]["reference"] == ref


def test_mcp_endpoint_mounted():
    """MCP エンドポイントが /mcp にマウントされていることを検証"""
    from ugh_audit.server import app as server_app

    mcp_routes = [
        r for r in server_app.routes
        if hasattr(r, "path") and r.path == "/mcp"
    ]
    assert len(mcp_routes) == 1


def test_mcp_session_manager_starts(client):
    """MCP セッションマネージャーがライフスパンで起動することを検証"""
    from ugh_audit.mcp_server import mcp as mcp_instance

    # TestClient のライフスパンで session_manager が起動済み
    assert mcp_instance.session_manager is not None
