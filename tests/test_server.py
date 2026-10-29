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
    with TestClient(app, headers={"X-UGH-Anthropic-Key": "offline-test-provider-key"}) as c:
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


def test_audit_computed_with_meta(client, tmp_db, monkeypatch, semantic_complete_judge):
    """確認済みの意味判定がある監査は computed のまま保存する。"""
    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)
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


def test_history_returns_computed_items(client, monkeypatch, semantic_complete_judge):
    """computed 結果のみ history に含まれる"""
    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)
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
    with TestClient(app, headers={"X-UGH-Anthropic-Key": "offline-test-provider-key"}) as c:
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


def test_legacy_advisory_collapse_cannot_downgrade_complete_requirements(
    monkeypatch, tmp_db, tmp_golden, semantic_complete_judge,
):
    """collapse_risk の従来 downgrade は参考診断に保存する。

    Codex P3 対応: 前提 (verdict=accept, mcg 計算済み) は必ず成立させる。
    成立しないときは fixture バグとして test を失敗させる（silent no-op 禁止）。

    mcg 計算には sentence-transformers (SBert) + grv_calculator が必要。
    CI の `[dev]` extra では未インストールのため skip する。
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        pytest.skip("sentence-transformers not installed (mcg computation unavailable)")

    import mode_grv

    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)

    # 閾値を下げ、accept + 非 None mcg なら必ず両ルール発火するようにする。
    # _TAU_ANCHOR_LOW=1.0 で anchor ルールは常に発火、_TAU_COLLAPSE_HIGH=0.0
    # で collapse ルールは非 None のとき常に発火。
    monkeypatch.setattr(mode_grv, "_TAU_COLLAPSE_HIGH", 0.0)
    monkeypatch.setattr(mode_grv, "_TAU_ANCHOR_LOW", 1.0)

    configure(db=tmp_db, golden=tmp_golden)
    with TestClient(app, headers={"X-UGH-Anthropic-Key": "offline-test-provider-key"}) as c:
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

    assert data["verdict_advisory"] == "accept"
    assert data["advisory_flags"] == []
    assert data["legacy_metrics"]["verdict_advisory"] == "rewrite"
    assert "mcg_collapse_downgrade" in data["legacy_metrics"]["advisory_flags"]
    assert "mcg_anchor_missing" in data["legacy_metrics"]["advisory_flags"]


def test_complete_requirements_override_legacy_rewrite(client, monkeypatch, semantic_complete_judge):
    """従来の低被覆率による rewrite は、検証済み意味充足を覆さない。"""
    from ugh_calculator import Evidence

    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)
    # Isolate advisory behavior with a known, reliable legacy rewrite (C=1/3).
    # Topic failure can instead be explicitly unreliable under the new guard.
    monkeypatch.setattr("ugh_audit.server._detect", lambda qid, response, meta: Evidence(
        question_id=qid, propositions_total=3, propositions_hit=1,
        hit_ids=[0], miss_ids=[1, 2], f4_premise=0.0,
    ))
    resp = client.post("/api/audit", json={
        "question": "質問A",
        "response": "命題とは無関係な回答です。",
        "question_meta": {
            "question": "質問A",
            "core_propositions": ["全く別の命題", "さらに別の命題", "もう一つの命題"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    data = resp.json()
    assert data["legacy_metrics"]["verdict"] == "rewrite"
    assert data["verdict"] == "accept"
    assert data["verdict_advisory"] == data["verdict"]
    assert data["advisory_flags"] == []


# --- Paper defense: hit_sources 公開 (core vs cascade 分離) ---


def test_hit_sources_empty_without_meta(client):
    """要件不在なら空の意味照合カウンターを公開する。"""
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["hit_sources"] == {
        "source": "llm_semantic", "fulfilled": 0, "missing": 0,
        "unknown": 0, "total": 0, "per_proposition": {},
    }
    assert data["legacy_metrics"]["hit_sources"] is None


def test_hit_sources_structure_with_meta(client):
    """question_meta ありで hit_sources が構造化サマリを返す"""
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
    hs = resp.json()["hit_sources"]
    assert hs is not None
    # 必須キー全て
    for key in ("source", "fulfilled", "missing", "unknown", "total", "per_proposition"):
        assert key in hs, f"{key} missing from hit_sources"
    # 内部整合
    assert hs["fulfilled"] + hs["missing"] + hs["unknown"] == hs["total"]
    assert hs["source"] == "llm_semantic"
    assert hs["total"] == 1  # 命題 1 件
    # per_proposition の key は文字列（JSON 互換）
    for k in hs["per_proposition"]:
        assert isinstance(k, str)
    for v in hs["per_proposition"].values():
        assert v in ("fulfilled", "missing", "unknown")


def test_hit_sources_core_only_claim_matches_tfidf_count(client):
    """`core_only_hit_rate` の分子は per_proposition の tfidf 件数と一致"""
    resp = client.post("/api/audit", json={
        "question": "PoRとは何か？",
        "response": "PoRは共鳴度である。意味との共振プロセスでもある。",
        "question_meta": {
            "question": "PoRとは何か？",
            "core_propositions": ["PoRは共鳴度", "PoRは共振プロセス"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    hs = resp.json()["legacy_metrics"]["hit_sources"]
    assert hs is not None
    tfidf_count = sum(1 for v in hs["per_proposition"].values() if v == "tfidf")
    assert hs["core_hit"] == tfidf_count
    assert hs["core_only_hit_rate"].startswith(f"{tfidf_count}/")


# ---------------------------------------------------------------------------
# degraded_reason enrichment (schema_version 2.1.0)
# ---------------------------------------------------------------------------


def test_degraded_reason_contains_question_meta_missing(client):
    """question_meta 未提供時は degraded_reason=['question_meta_missing', ...]"""
    resp = client.post("/api/audit", json={
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
    })
    data = resp.json()
    assert data["verdict"] == "degraded"
    assert "semantic_matching_undetermined" in data["degraded_reason"]
    assert "question_meta_missing" in data["legacy_metrics"]["errors"]


def test_degraded_reason_empty_when_not_degraded(client, monkeypatch, semantic_complete_judge):
    """非 degraded 時は degraded_reason=[] (空リスト)"""
    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)
    resp = client.post("/api/audit", json={
        "question": "PoRとは何か？",
        "response": "PoRは共鳴度である。意味との共振プロセスでもある。",
        "question_meta": {
            "question": "PoRとは何か？",
            "core_propositions": ["PoRは共鳴度", "PoRは共振プロセス"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    data = resp.json()
    assert data["verdict"] != "degraded"
    assert data["degraded_reason"] == []


def test_degraded_reason_detector_unavailable(client, monkeypatch):
    """detector モジュールが import 失敗時、degraded_reason に 'detector_unavailable' を含む"""
    from ugh_audit import server as server_module

    monkeypatch.setattr(server_module, "_HAS_DETECTOR", False)
    resp = client.post("/api/audit", json={
        "question": "PoRとは何か？",
        "response": "PoRは共鳴度である。",
        "question_meta": {
            "question": "PoRとは何か？",
            "core_propositions": ["PoRは共鳴度"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    data = resp.json()
    assert data["verdict"] == "degraded"
    assert "semantic_matching_undetermined" in data["degraded_reason"]
    assert "detector_unavailable" in data["legacy_metrics"]["errors"]


def test_degraded_reason_detector_error(client, monkeypatch):
    """detector が例外を送出時、degraded_reason に 'detector_error:<type>' を含む"""
    from ugh_audit import server as server_module

    def _raise(*_args, **_kwargs):
        raise RuntimeError("synthetic detector failure")

    monkeypatch.setattr(server_module, "_detect", _raise)
    resp = client.post("/api/audit", json={
        "question": "PoRとは何か？",
        "response": "PoRは共鳴度である。",
        "question_meta": {
            "question": "PoRとは何か？",
            "core_propositions": ["PoRは共鳴度"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        },
    })
    data = resp.json()
    # detector が落ちても pipeline は degraded で継続
    assert data["verdict"] == "degraded"
    assert "semantic_matching_undetermined" in data["degraded_reason"]
    assert "detector_error:RuntimeError" in data["legacy_metrics"]["errors"]
    # detector fault と metadata 問題を区別するため、以下の誤った reason が
    # 混入してはならない (core_propositions は実際に提供されており、
    # detection は skip ではなく例外で失敗しただけ)
    assert "core_propositions_missing" not in data["degraded_reason"]
    assert "detection_skipped" not in data["degraded_reason"]


def test_schema_version_4_0_0(client):
    """意味充足の判定と従来診断を分離したスキーマは 4.0.0。"""
    resp = client.post("/api/audit", json={
        "question": "テスト",
        "response": "テスト回答",
    })
    assert resp.json()["schema_version"] == "4.0.0"


def test_metadata_without_semantic_credentials_never_accepts(client, monkeypatch):
    """メタデータだけで従来acceptへ戻らない。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post("/api/audit", json={
        "question": "資料を用意する手順は？",
        "response": "資料を3部用意します。",
        "question_meta": {"core_propositions": ["資料を3部用意する"], "trap_type": ""},
    })
    data = response.json()
    assert data["verdict"] == data["verdict_advisory"] == "degraded"
    assert data["requirement_verdict"] == "undetermined"
    assert data["requires_review"] is True
    assert data["semantic_matching"]["requirements_total"] == 1
    assert data["semantic_matching"]["unknown_count"] == 1
    assert data["C"] is None
    assert data["legacy_metrics"]["C"] == 1.0
    assert data["saved_id"] is None
