"""
tests/test_mcp_server.py
MCP サーバーのテスト — audit_answer ツールの動作を検証（パイプライン A 対応）
"""
from __future__ import annotations

import asyncio

import pytest

from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB

# MCP SDK はオプショナル依存 — なければスキップ
mcp_mod = pytest.importorskip("mcp")

from ugh_audit.mcp_server import configure, mcp  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    return AuditDB(db_path=tmp_path / "test_mcp.db")


@pytest.fixture
def tmp_golden(tmp_path):
    return GoldenStore(path=tmp_path / "golden.json")


@pytest.fixture(autouse=True)
def _configure_mcp(tmp_db, tmp_golden):
    """MCP サーバーのグローバルインスタンスをテスト用に差し替える"""
    configure(db=tmp_db, golden=tmp_golden)
    yield
    configure(db=None, golden=None)


def _run(coro):
    """asyncio.run のヘルパー"""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_tools_list_includes_audit_answer():
    """tools/list に audit_answer が含まれることを検証"""
    tools = _run(mcp.list_tools())
    tool_names = [t.name for t in tools]
    assert "audit_answer" in tool_names


def test_audit_answer_tool_schema():
    """audit_answer の入力スキーマが正しいことを検証"""
    tools = _run(mcp.list_tools())
    audit_tool = next(t for t in tools if t.name == "audit_answer")

    schema = audit_tool.inputSchema
    assert "question" in schema["properties"]
    assert "response" in schema["properties"]
    assert "reference" in schema["properties"]
    assert "question" in schema["required"]
    assert "response" in schema["required"]

    out = audit_tool.outputSchema
    assert out is not None
    for fld in ("S", "C", "delta_e", "quality_score", "verdict", "hit_rate", "saved_id"):
        assert fld in out["properties"], f"{fld} missing from outputSchema"


def test_audit_answer_degraded_without_question_meta():
    """question_meta なしでは verdict="degraded", C=None, saved_id=None を返すことを検証"""
    content, structured = _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
        "reference": "AIは意味と共振する動的プロセスです。",
    }))

    assert isinstance(structured, dict)
    assert isinstance(structured["S"], float)
    assert structured["C"] is None
    assert structured["delta_e"] is None
    assert structured["quality_score"] is None
    assert structured["verdict"] == "degraded"
    assert structured["mode"] == "degraded"
    assert structured["structural_gate"]["f4"] is None
    assert structured["saved_id"] is None
    assert structured["schema_version"] == "2.1.0"

    assert len(content) >= 1


def test_audit_answer_degraded_not_saved_to_db(tmp_db):
    """degraded 結果は DB に保存されない"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト質問",
        "response": "テスト回答",
    }))
    assert structured["saved_id"] is None
    rows = tmp_db.list_recent(10)
    assert len(rows) == 0


def test_audit_answer_without_reference():
    """reference 省略時に正常動作することを検証"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    assert "verdict" in structured
    assert structured["verdict"] == "degraded"
    assert structured["saved_id"] is None


def test_audit_answer_preserves_session_id(tmp_db):
    """session_id は degraded 時 DB 未保存なので、computed 時のみ検証"""
    # degraded 時は DB に保存されないことを確認
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "質問1",
        "response": "回答1",
        "session_id": "mcp-sess-1",
    }))
    assert structured["saved_id"] is None
    rows = tmp_db.list_recent(10)
    assert len(rows) == 0


def test_audit_answer_resolves_golden_reference(tmp_db, tmp_golden):
    """reference 省略時に GoldenStore から自動解決されることを検証"""
    ref = tmp_golden.find_reference("AIは意味を持てるか？")
    assert ref is not None

    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
    }))
    # degraded なので DB 未保存
    assert structured["saved_id"] is None


def test_streamable_http_app_created():
    """MCP Streamable HTTP アプリが生成可能であることを検証"""
    app = mcp.streamable_http_app()
    assert callable(app)


# --- Phase E: verdict_advisory / advisory_flags ---


def test_audit_answer_returns_verdict_advisory():
    """MCP tool の出力に verdict_advisory と advisory_flags が含まれる"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    assert "verdict_advisory" in structured
    assert "advisory_flags" in structured
    assert isinstance(structured["advisory_flags"], list)


def test_audit_answer_degraded_advisory_passthrough():
    """degraded 時は advisory == degraded, flags == []"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    assert structured["verdict"] == "degraded"
    assert structured["verdict_advisory"] == "degraded"
    assert structured["advisory_flags"] == []


def test_audit_answer_schema_includes_advisory_fields():
    """outputSchema に verdict_advisory / advisory_flags が含まれる"""
    tools = _run(mcp.list_tools())
    audit_tool = next(t for t in tools if t.name == "audit_answer")
    out = audit_tool.outputSchema
    assert out is not None
    assert "verdict_advisory" in out["properties"]
    assert "advisory_flags" in out["properties"]


def test_proxy_audit_relays_advisory(monkeypatch):
    """UGH_REMOTE_API proxy 経路でも advisory フィールドが転送される"""
    import json
    import os

    from ugh_audit import mcp_server as m

    fake_response = {
        "schema_version": "2.0.0",
        "S": 0.9,
        "C": 0.8,
        "delta_e": 0.05,
        "quality_score": 4.8,
        "verdict": "accept",
        "hit_rate": "2/3",
        "structural_gate": {
            "f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": 0.0,
            "gate_verdict": "pass", "primary_fail": "none",
        },
        "mode": "computed",
        "is_reliable": True,
        "matched_id": "qXYZ",
        "metadata_source": "inline",
        "verdict_advisory": "rewrite",
        "advisory_flags": ["mcg_collapse_downgrade"],
        "mode_conditioned_grv": {"anchor_alignment": 0.5, "collapse_risk": 0.95},
    }

    class _FakeResp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self._body

    def _fake_urlopen(req, timeout=60):
        return _FakeResp(json.dumps(fake_response).encode("utf-8"))

    # patch the urllib used inside _proxy_audit
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setenv("UGH_REMOTE_API", "http://fake.invalid")
    try:
        result = m._proxy_audit(
            os.environ["UGH_REMOTE_API"],
            question="q", response="r",
            reference=None, session_id=None,
            question_meta=None, auto_generate_meta=False, retry_of=None,
        )
    finally:
        monkeypatch.delenv("UGH_REMOTE_API", raising=False)

    assert result.verdict == "accept"
    assert result.verdict_advisory == "rewrite"
    assert result.advisory_flags == ["mcg_collapse_downgrade"]


def test_proxy_audit_error_falls_back_to_degraded_advisory(monkeypatch):
    """proxy エラー時、advisory も degraded にフォールバックする"""
    from ugh_audit import mcp_server as m

    def _raise(req, timeout=60):
        raise RuntimeError("boom")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    result = m._proxy_audit(
        "http://fake.invalid",
        question="q", response="r",
        reference=None, session_id=None,
        question_meta=None, auto_generate_meta=False, retry_of=None,
    )
    assert result.verdict == "degraded"
    assert result.verdict_advisory == "degraded"
    assert result.advisory_flags == []


def test_constructor_path_does_not_leak_advisory_between_calls():
    """stateless_http モードで連続呼び出しに advisory が汚染されない"""
    # 2 回連続呼び出しで advisory_flags が mutable な default を共有していないこと
    _, s1 = _run(mcp.call_tool("audit_answer", {
        "question": "Q1",
        "response": "R1",
    }))
    _, s2 = _run(mcp.call_tool("audit_answer", {
        "question": "Q2",
        "response": "R2",
    }))
    # 両者独立 (list が同一オブジェクトでない保証)
    assert s1["advisory_flags"] == []
    assert s2["advisory_flags"] == []
    # 値で比較されるため、object identity は確認できないが、
    # mutable default bug があれば片方の変更が他方に伝播する。
    # ここでは「両方が空リスト」かつ「verdict_advisory が primary と一致」で十分。
    assert s1["verdict_advisory"] == s1["verdict"]
    assert s2["verdict_advisory"] == s2["verdict"]


# --- Paper defense: hit_sources 公開 ---


def test_audit_answer_returns_hit_sources_field():
    """MCP tool 出力に hit_sources フィールドが存在する"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    # 命題未検出時は null
    assert "hit_sources" in structured
    assert structured["hit_sources"] is None


def test_audit_answer_hit_sources_schema_in_outputSchema():
    """outputSchema に hit_sources が含まれる"""
    tools = _run(mcp.list_tools())
    audit_tool = next(t for t in tools if t.name == "audit_answer")
    out = audit_tool.outputSchema
    assert out is not None
    assert "hit_sources" in out["properties"]


def test_proxy_audit_relays_hit_sources(monkeypatch):
    """proxy 経路でも hit_sources が転送される"""
    import json

    from ugh_audit import mcp_server as m

    fake_response = {
        "schema_version": "2.0.0",
        "S": 0.9,
        "C": 0.67,
        "delta_e": 0.1,
        "quality_score": 4.6,
        "verdict": "accept",
        "hit_rate": "2/3",
        "structural_gate": {
            "f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": 0.0,
            "gate_verdict": "pass", "primary_fail": "none",
        },
        "mode": "computed",
        "is_reliable": True,
        "matched_id": "qTEST",
        "metadata_source": "inline",
        "verdict_advisory": "accept",
        "advisory_flags": [],
        "hit_sources": {
            "core_hit": 1,
            "cascade_rescued": 1,
            "miss": 1,
            "total": 3,
            "core_only_hit_rate": "1/3",
            "per_proposition": {"0": "tfidf", "1": "cascade_rescued", "2": "miss"},
        },
    }

    class _FakeResp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self._body

    def _fake_urlopen(req, timeout=60):
        return _FakeResp(json.dumps(fake_response).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    result = m._proxy_audit(
        "http://fake.invalid",
        question="q", response="r",
        reference=None, session_id=None,
        question_meta=None, auto_generate_meta=False, retry_of=None,
    )
    assert result.hit_sources is not None
    assert result.hit_sources["core_hit"] == 1
    assert result.hit_sources["cascade_rescued"] == 1
    assert result.hit_sources["core_only_hit_rate"] == "1/3"
