"""
ugh_audit/mcp_server.py
MCP サーバー — ChatGPT Connectors 用

Model Context Protocol (MCP) で audit_answer ツールを公開する。
ChatGPT Settings > Connectors から MCP URL を登録して利用する。

起動方法:
    python -m ugh_audit.mcp_server                 # Streamable HTTP (port 8000)
    python -m ugh_audit.mcp_server --port 9000      # ポート指定
"""
from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .reference.golden_store import GoldenStore
from .scorer.ugh_scorer import UGHScorer
from .storage.audit_db import AuditDB

# ---------------------------------------------------------------------------
# MCP サーバー定義
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "UGH Audit",
    instructions=(
        "AI回答の意味論的監査ツール。"
        "UGHer の3指標 (PoR / ΔE / grv) で意味的誠実性を定量評価する。"
    ),
)

# ---------------------------------------------------------------------------
# 共有インスタンス（遅延初期化）
# ---------------------------------------------------------------------------

_scorer: Optional[UGHScorer] = None
_db: Optional[AuditDB] = None
_golden: Optional[GoldenStore] = None


def _get_scorer() -> UGHScorer:
    global _scorer
    if _scorer is None:
        _scorer = UGHScorer(model_id="chatgpt-mcp")
    return _scorer


def _get_db() -> AuditDB:
    global _db
    if _db is None:
        _db = AuditDB()
    return _db


def _get_golden() -> GoldenStore:
    global _golden
    if _golden is None:
        _golden = GoldenStore()
    return _golden


def configure(
    db: Optional[AuditDB] = None,
    scorer: Optional[UGHScorer] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """テストやカスタム設定用にグローバルインスタンスを差し替える"""
    global _scorer, _db, _golden
    if db is not None:
        _db = db
    if scorer is not None:
        _scorer = scorer
    if golden is not None:
        _golden = golden


# ---------------------------------------------------------------------------
# ツール定義
# ---------------------------------------------------------------------------


@mcp.tool()
def audit_answer(
    question: str,
    response: str,
    reference: Optional[str] = None,
) -> str:
    """AI回答を意味監査する。

    question（質問）、response（AI回答）、reference（期待回答、省略可）を受け取り、
    PoR（意味共鳴度）、ΔE（意味ズレ量）、grv（語彙重力分布）、verdict（判定）を返す。
    結果はDBに自動保存される。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
    """
    scorer = _get_scorer()
    db = _get_db()
    golden = _get_golden()

    # reference の自動解決
    ref = reference or golden.find_reference(question)

    result = scorer.score(
        question=question,
        response=response,
        reference=ref,
    )
    saved_id = db.save(result)

    return json.dumps(
        {
            "por": round(result.por, 4),
            "delta_e": round(result.delta_e, 4),
            "grv": {k: round(v, 4) for k, v in result.grv.items()},
            "verdict": result.meaning_drift,
            "saved_id": saved_id,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# スタンドアロン起動
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="UGH Audit MCP Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")
