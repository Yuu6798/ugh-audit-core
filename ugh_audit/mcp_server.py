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

from dataclasses import dataclass
from typing import Dict, Optional

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
    # server.py の app.mount("/mcp", ...) と組み合わせた際に
    # /mcp/mcp にならないよう内部パスを "/" に設定
    streamable_http_path="/",
    # マルチワーカー / ロードバランサー環境でセッション喪失を防ぐ
    stateless_http=True,
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
# 出力データクラス
# ---------------------------------------------------------------------------


@dataclass
class AuditOutput:
    """audit_answer ツールの構造化出力"""

    por: float
    delta_e: float
    grv: Dict[str, float]
    verdict: str
    saved_id: int


# ---------------------------------------------------------------------------
# ツール定義
# ---------------------------------------------------------------------------


@mcp.tool()
def audit_answer(
    question: str,
    response: str,
    reference: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AuditOutput:
    """AI回答を意味監査する。

    question（質問）、response（AI回答）、reference（期待回答、省略可）を受け取り、
    PoR（意味共鳴度）、ΔE（意味ズレ量）、grv（語彙重力分布）、verdict（判定）を返す。
    結果はDBに自動保存される。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
        session_id: セッションID（省略時は自動生成、同一会話の複数ターンを紐付ける）
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
        session_id=session_id,
    )
    saved_id = db.save(result)

    return AuditOutput(
        por=round(result.por, 4),
        delta_e=round(result.delta_e, 4),
        grv={k: round(v, 4) for k, v in result.grv.items()},
        verdict=result.meaning_drift,
        saved_id=saved_id,
    )


# ---------------------------------------------------------------------------
# スタンドアロン起動
# ---------------------------------------------------------------------------

_MCP_CORS_ORIGINS = ["https://chat.openai.com", "https://chatgpt.com"]

if __name__ == "__main__":
    import argparse

    from starlette.middleware.cors import CORSMiddleware

    parser = argparse.ArgumentParser(description="UGH Audit MCP Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = "/mcp"

    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_MCP_CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
