"""
tests/test_pipeline_parity.py
REST (server.py) と MCP (mcp_server.py) が同一入力に対して主要フィールドで
一致することを検証する (pipeline.py への統合後の回帰テスト)。

観点: `_run_pipeline` / inline pipeline を `ugh_audit.pipeline.run_audit` に
統合したため、どちら経由でも同じ監査ロジックが走るはず。主要フィールド
(S, C, delta_e, verdict, mode, degraded_reason, schema_version) の一致を
直接比較する。
"""
from __future__ import annotations

import pytest

from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from ugh_audit.mcp_server import audit_answer as mcp_audit  # noqa: E402
from ugh_audit.mcp_server import configure as configure_mcp  # noqa: E402
from ugh_audit.server import app, configure as configure_server  # noqa: E402


@pytest.fixture
def shared_infra(tmp_path):
    """REST と MCP で同じ DB / GoldenStore を共有する"""
    db = AuditDB(db_path=tmp_path / "parity.db")
    golden = GoldenStore(path=tmp_path / "parity_golden.json")
    configure_server(db=db, golden=golden)
    configure_mcp(db=db, golden=golden)
    yield db, golden
    configure_server(db=None, golden=None)
    configure_mcp(db=None, golden=None)


@pytest.fixture
def rest_client(shared_infra):
    with TestClient(app) as c:
        yield c


_PARITY_CASES = [
    pytest.param(
        {
            "question": "質問のみ",
            "response": "回答のみ",
        },
        id="degraded_no_meta",
    ),
    pytest.param(
        {
            "question": "PoRとは何か？",
            "response": "PoRは共鳴度である。意味との共振プロセスでもある。",
            "question_meta": {
                "question": "PoRとは何か？",
                "core_propositions": ["PoRは共鳴度", "PoRは共振プロセス"],
                "disqualifying_shortcuts": [],
                "acceptable_variants": [],
                "trap_type": "",
            },
        },
        id="computed_with_meta",
    ),
]


@pytest.mark.parametrize("payload", _PARITY_CASES)
def test_server_mcp_parity_core_fields(rest_client, payload):
    """REST と MCP で核となるスカラーフィールドが一致する"""
    rest_resp = rest_client.post("/api/audit", json=payload).json()
    mcp_resp = mcp_audit(**payload)

    for field in (
        "schema_version",
        "S",
        "C",
        "delta_e",
        "quality_score",
        "verdict",
        "mode",
        "is_reliable",
        "metadata_source",
        "hit_rate",
    ):
        assert rest_resp[field] == getattr(mcp_resp, field), (
            f"REST/MCP 乖離 ({field}): rest={rest_resp[field]!r} "
            f"mcp={getattr(mcp_resp, field)!r}"
        )


@pytest.mark.parametrize("payload", _PARITY_CASES)
def test_server_mcp_parity_error_fields(rest_client, payload):
    """degraded_reason / errors / computed_components / missing_components が一致する"""
    rest_resp = rest_client.post("/api/audit", json=payload).json()
    mcp_resp = mcp_audit(**payload)

    assert rest_resp["degraded_reason"] == mcp_resp.degraded_reason
    assert rest_resp["errors"] == mcp_resp.errors
    assert rest_resp["computed_components"] == mcp_resp.computed_components
    assert rest_resp["missing_components"] == mcp_resp.missing_components


def test_server_mcp_parity_structural_gate(rest_client):
    """structural_gate (f1-f4, gate_verdict, primary_fail) が一致する"""
    payload = _PARITY_CASES[1].values[0]
    rest_resp = rest_client.post("/api/audit", json=payload).json()
    mcp_resp = mcp_audit(**payload)

    rest_gate = rest_resp["structural_gate"]
    mcp_gate = mcp_resp.structural_gate
    for key in ("f1", "f2", "f3", "gate_verdict", "primary_fail"):
        assert rest_gate[key] == mcp_gate[key], (
            f"structural_gate[{key}] 乖離: rest={rest_gate[key]!r} mcp={mcp_gate[key]!r}"
        )
