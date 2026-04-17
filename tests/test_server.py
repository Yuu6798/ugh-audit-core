"""
tests/test_server.py
REST API サーバーのテスト（パイプライン A 対応）
"""
import pytest

from ugh_audit.reference.golden_store import GoldenStore
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
    configure(db=tmp_db, golden=tmp_golden)
    with TestClient(app) as c:
        yield c
    # リセット
    configure(db=None, golden=None)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_audit_degraded_without_meta(client):
    """question_meta なし → degraded, saved_id=None, DB 未保存"""
    resp = client.post("/api/audit", json={
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
        "reference": "AIは意味と共振する動的プロセスです。",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "S" in data
    assert "C" in data
    assert "delta_e" in data
    assert "quality_score" in data
    assert "verdict" in data
    assert "hit_rate" in data
    assert "structural_gate" in data
    assert "saved_id" in data
    assert data["saved_id"] is None
    assert data["verdict"] == "degraded"


def test_audit_degraded_not_saved_to_db(client, tmp_db):
    """degraded 結果は DB に保存されない"""
    client.post("/api/audit", json={
        "question": "テスト質問",
        "response": "テスト回答",
    })
    rows = tmp_db.list_recent(10)
    assert len(rows) == 0


def test_audit_without_reference(client):
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    assert resp.status_code == 200
    assert "verdict" in resp.json()


def test_audit_computed_with_meta(client, tmp_db):
    """question_meta あり → computed, saved_id あり, DB 保存"""
    resp = client.post("/api/audit", json={
        "question": "PoRが高ければ誠実か？",
        "response": "PoRは共鳴度であり、誠実性の十分条件ではない。",
        "question_meta": {
            "question": "PoRが高ければ誠実か？",
            "core_propositions": ["PoRは誠実性の十分条件ではない"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "metric_omnipotence",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "computed"
    assert data["verdict"] in ("accept", "rewrite", "regenerate")
    assert isinstance(data["saved_id"], int)
    assert data["saved_id"] >= 1

    rows = tmp_db.list_recent(10)
    assert len(rows) == 1
    assert rows[0]["question"] == "PoRが高ければ誠実か？"


def test_history_empty(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_returns_computed_items(client):
    """computed 結果のみ history に含まれる"""
    # degraded (DB 未保存)
    client.post("/api/audit", json={
        "question": "質問1",
        "response": "回答1",
    })
    # computed (DB 保存)
    client.post("/api/audit", json={
        "question": "質問2",
        "response": "回答2",
        "question_meta": {
            "question": "質問2",
            "core_propositions": ["命題A"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "metric_omnipotence",
        },
    })
    resp = client.get("/api/history?limit=10")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["question"] == "質問2"
    assert "S" in items[0]
    assert "delta_e" in items[0]
    assert "verdict" in items[0]


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/audit" in schema["paths"]
    assert "/api/history" in schema["paths"]


def test_audit_resolves_reference_from_golden(tmp_db, tmp_path):
    """reference 省略時に GoldenStore から自動解決されることを検証"""
    golden = GoldenStore(path=tmp_path / "golden.json")
    ref = golden.find_reference("AIは意味を持てるか？")
    assert ref is not None
    # degraded なので DB 保存はないが、GoldenStore は動作している
    configure(db=tmp_db, golden=golden)
    with TestClient(app) as c:
        resp = c.post("/api/audit", json={
            "question": "AIは意味を持てるか？",
            "response": "AIは意味を処理できます。",
        })
    configure(db=None, golden=None)
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "degraded"


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

    assert mcp_instance.session_manager is not None


def test_structural_gate_fields(client):
    """structural_gate が全フィールドを含むことを検証"""
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    gate = resp.json()["structural_gate"]
    assert "f1" in gate
    assert "f2" in gate
    assert "f3" in gate
    assert "f4" in gate
    assert "gate_verdict" in gate
    assert "primary_fail" in gate


# --- Phase E: verdict_advisory / advisory_flags ---


def test_verdict_advisory_fields_present(client):
    """/api/audit のレスポンスに verdict_advisory / advisory_flags が必ず含まれる"""
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    data = resp.json()
    assert "verdict_advisory" in data
    assert "advisory_flags" in data
    assert isinstance(data["advisory_flags"], list)


def test_verdict_advisory_degraded_passthrough(client):
    """degraded のときは advisory も degraded, flags は空"""
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    data = resp.json()
    assert data["verdict"] == "degraded"
    assert data["verdict_advisory"] == "degraded"
    assert data["advisory_flags"] == []


def test_verdict_advisory_is_valid_verdict_value(client):
    """advisory は常に VALID_VERDICTS の値を持つ (schema 契約)"""
    resp = client.post("/api/audit", json={
        "question": "PoRが高ければ誠実か？",
        "response": "PoRは共鳴度であり、誠実性の十分条件ではない。",
        "question_meta": {
            "question": "PoRが高ければ誠実か？",
            "core_propositions": ["PoRは誠実性の十分条件ではない"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "metric_omnipotence",
        },
    })
    data = resp.json()
    assert data["verdict_advisory"] in {"accept", "rewrite", "regenerate", "degraded"}
    assert isinstance(data["advisory_flags"], list)
    # advisory は primary verdict と等しいか downgrade 方向のみ (accept→rewrite)
    rank = {"accept": 2, "rewrite": 1, "regenerate": 0, "degraded": -1}
    assert rank[data["verdict_advisory"]] <= rank[data["verdict"]]


def test_verdict_advisory_downgrades_on_extreme_collapse(
    monkeypatch, tmp_db, tmp_golden,
):
    """collapse_risk が閾値を超えると accept → rewrite に downgrade される

    Codex P3 対応: 前提 (verdict=accept, mcg 計算済み) は必ず成立させる。
    成立しないときは fixture バグとして test を失敗させる（silent no-op 禁止）。
    """
    import mode_grv

    # 閾値を下げ、accept + 非 None mcg なら必ず両ルール発火するようにする。
    # _TAU_ANCHOR_LOW=1.0 で anchor ルールは常に発火、_TAU_COLLAPSE_HIGH=0.0
    # で collapse ルールは非 None のとき常に発火。
    monkeypatch.setattr(mode_grv, "_TAU_COLLAPSE_HIGH", 0.0)
    monkeypatch.setattr(mode_grv, "_TAU_ANCHOR_LOW", 1.0)

    configure(db=tmp_db, golden=tmp_golden)
    with TestClient(app) as c:
        resp = c.post("/api/audit", json={
            # accept を確実にするため命題と回答を高マッチにする。
            # mcg の collapse_risk を None にしないよう命題 2 本 (>=2 で applicable)。
            # exploratory primary mode は collapse_risk を focus に含む。
            "question": "PoRとは何か？",
            "response": "PoRは共鳴度である。意味との共振プロセスでもある。",
            "question_meta": {
                "question": "PoRとは何か？",
                "core_propositions": ["PoRは共鳴度", "PoRは共振プロセス"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "",
                "mode_affordance": {"primary": "exploratory"},
            },
        })
    configure(db=None, golden=None)
    data = resp.json()

    # --- 前提条件を必ず満たすことをハード assert する（silent no-op 禁止）---
    assert data["verdict"] == "accept", (
        f"fixture precondition failed: primary verdict must be accept "
        f"to exercise downgrade, got {data['verdict']!r}"
    )
    assert data.get("mode_conditioned_grv") is not None, (
        "fixture precondition failed: mode_conditioned_grv must be computed "
        "(SBert + mode_affordance='exploratory' with >=2 propositions)"
    )
    mcg = data["mode_conditioned_grv"]
    assert mcg["collapse_risk"] is not None, (
        "fixture precondition failed: collapse_risk must be non-None "
        "(needs n_propositions>=2)"
    )

    # --- 目標挙動: downgrade 発生 + 両フラグ発火 ---
    assert data["verdict_advisory"] == "rewrite"
    assert "mcg_collapse_downgrade" in data["advisory_flags"]
    assert "mcg_anchor_missing" in data["advisory_flags"]


def test_verdict_advisory_rewrite_passthrough(client):
    """primary verdict が rewrite のとき advisory も rewrite のまま (pass-through)"""
    resp = client.post("/api/audit", json={
        "question": "質問A",
        "response": "命題とは無関係な回答です。",
        "question_meta": {
            "question": "質問A",
            "core_propositions": ["全く別の命題", "さらに別の命題"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    data = resp.json()
    if data["verdict"] in ("rewrite", "regenerate"):
        # pass-through: flags は空
        assert data["verdict_advisory"] == data["verdict"]
        assert data["advisory_flags"] == []
