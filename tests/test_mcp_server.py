"""
tests/test_mcp_server.py
MCP サーバーのテスト — audit_answer ツールの動作を検証
"""
from __future__ import annotations

import asyncio

import pytest

from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.scorer.ugh_scorer import UGHScorer
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
    scorer = UGHScorer(model_id="test-mcp")
    configure(db=tmp_db, scorer=scorer, golden=tmp_golden)
    yield
    configure(db=None, scorer=None, golden=None)


def _run(coro):
    """asyncio.run のヘルパー"""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_tools_list_includes_audit_answer():
    """tools/list に audit_answer が含まれることを検証"""
    tools = _run(mcp.list_tools())
    tool_names = [t.name for t in tools]
    assert "audit_answer" in tool_names


def test_audit_answer_tool_schema():
    """audit_answer の入力/出力スキーマが正しいことを検証"""
    tools = _run(mcp.list_tools())
    audit_tool = next(t for t in tools if t.name == "audit_answer")

    # 入力スキーマ
    schema = audit_tool.inputSchema
    assert "question" in schema["properties"]
    assert "response" in schema["properties"]
    assert "reference" in schema["properties"]
    assert "question" in schema["required"]
    assert "response" in schema["required"]

    # 出力スキーマ（構造化出力）
    out = audit_tool.outputSchema
    assert out is not None
    for field in ("por", "delta_e", "grv", "verdict", "saved_id"):
        assert field in out["properties"], f"{field} missing from outputSchema"


def test_audit_answer_returns_structured_output():
    """tools/call で構造化出力が返ることを検証"""
    content, structured = _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
        "reference": "AIは意味と共振する動的プロセスです。",
    }))

    # structured dict に全フィールドが含まれる
    assert isinstance(structured, dict)
    assert isinstance(structured["por"], float)
    assert isinstance(structured["delta_e"], float)
    assert isinstance(structured["grv"], dict)
    assert isinstance(structured["verdict"], str)
    assert isinstance(structured["saved_id"], int)
    assert structured["saved_id"] >= 1

    # content にもテキスト表現が含まれる
    assert len(content) >= 1
    assert "por" in content[0].text


def test_audit_answer_saves_to_db(tmp_db):
    """audit_answer の結果が DB に保存されることを検証"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト質問",
        "response": "テスト回答",
    }))
    rows = tmp_db.list_recent(10)
    assert len(rows) == 1
    assert rows[0]["question"] == "テスト質問"
    assert structured["saved_id"] == rows[0]["id"]


def test_audit_answer_without_reference():
    """reference 省略時に正常動作することを検証"""
    _, structured = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    assert "verdict" in structured
    assert structured["saved_id"] >= 1


def test_audit_answer_preserves_session_id(tmp_db):
    """session_id を指定すると同一値で DB に保存されることを検証"""
    for i in range(2):
        _run(mcp.call_tool("audit_answer", {
            "question": f"質問{i}",
            "response": f"回答{i}",
            "session_id": "mcp-sess-1",
        }))
    rows = tmp_db.list_recent(10)
    assert len(rows) == 2
    assert all(r["session_id"] == "mcp-sess-1" for r in rows)


def test_audit_answer_resolves_golden_reference(tmp_db, tmp_golden):
    """reference 省略時に GoldenStore から自動解決されることを検証"""
    ref = tmp_golden.find_reference("AIは意味を持てるか？")
    assert ref is not None

    _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
    }))

    rows = tmp_db.list_recent(1)
    assert rows[0]["reference"] == ref


def test_streamable_http_app_created():
    """MCP Streamable HTTP アプリが生成可能であることを検証"""
    app = mcp.streamable_http_app()
    assert callable(app)
