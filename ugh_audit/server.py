"""
ugh_audit/server.py
REST API + MCP サーバー

FastAPI ベースの HTTP API と MCP (Model Context Protocol) サーバーを提供する。
ChatGPT Connectors から MCP URL (http://<host>:<port>/mcp) を登録して利用可能。

主要エンドポイント:
    POST /api/audit   — REST: audit_answer (question/response/reference → スコア)
    GET  /api/history  — REST: 直近の監査履歴
    POST /mcp          — MCP: Streamable HTTP エンドポイント
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .reference.golden_store import GoldenStore
from .scorer.ugh_scorer import UGHScorer
from .storage.audit_db import AuditDB

# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------


class AuditRequest(BaseModel):
    """audit_answer ツールの入力"""

    question: str = Field(..., description="ユーザーの質問")
    response: str = Field(..., description="AIの回答")
    reference: Optional[str] = Field(
        None, description="期待される正解（省略時は GoldenStore から自動検索）"
    )


class AuditResponse(BaseModel):
    """audit_answer ツールの出力"""

    por: float = Field(..., description="Point of Resonance — 意味的共鳴度 (0–1)")
    delta_e: float = Field(..., description="ΔE — 意味ズレ量 (0–1)")
    grv: Dict[str, float] = Field(..., description="語彙重力分布")
    verdict: str = Field(..., description="判定: 同一意味圏 / 軽微なズレ / 意味乖離")
    saved_id: int = Field(..., description="DB保存時の行ID")


class HistoryItem(BaseModel):
    """履歴1件"""

    id: int
    question: str
    response: str
    por: float
    delta_e: float
    meaning_drift: str
    created_at: str


# ---------------------------------------------------------------------------
# アプリケーション
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UGH Audit",
    description=(
        "AI回答の意味論的監査ツール。"
        "UGHer の3指標 (PoR / ΔE / grv) で意味的誠実性を定量評価する。"
        "\n\nMCP エンドポイント: POST /mcp"
    ),
    version="0.2.0",
)

# CORS — ChatGPT Connectors からのリクエストを許可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "https://chatgpt.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# MCP サーバーをマウント (/mcp)
# ---------------------------------------------------------------------------

from .mcp_server import mcp as _mcp_instance  # noqa: E402

app.mount("/mcp", _mcp_instance.streamable_http_app())

# ---------------------------------------------------------------------------
# 共有インスタンス（起動時に初期化）
# ---------------------------------------------------------------------------

_scorer: Optional[UGHScorer] = None
_db: Optional[AuditDB] = None
_golden: Optional[GoldenStore] = None


def _get_scorer() -> UGHScorer:
    global _scorer
    if _scorer is None:
        _scorer = UGHScorer(model_id="chatgpt-connector")
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


# ---------------------------------------------------------------------------
# テスト用: DB/Scorer/GoldenStore を差し替える
# ---------------------------------------------------------------------------


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
# エンドポイント
# ---------------------------------------------------------------------------


@app.post(
    "/api/audit",
    response_model=AuditResponse,
    summary="audit_answer — AI回答を意味監査する",
    description=(
        "question / response / reference を受け取り、"
        "UGH指標 (PoR, ΔE, grv) でスコアリングして結果を返す。"
        "結果は自動的に SQLite に保存される。"
    ),
)
def audit_answer(req: AuditRequest) -> AuditResponse:
    scorer = _get_scorer()
    db = _get_db()
    golden = _get_golden()
    # reference の自動解決: 明示値 → GoldenStore → None (scorer が question にフォールバック)
    ref = req.reference or golden.find_reference(req.question)
    result = scorer.score(
        question=req.question,
        response=req.response,
        reference=ref,
    )
    saved_id = db.save(result)
    return AuditResponse(
        por=round(result.por, 4),
        delta_e=round(result.delta_e, 4),
        grv={k: round(v, 4) for k, v in result.grv.items()},
        verdict=result.meaning_drift,
        saved_id=saved_id,
    )


@app.get(
    "/api/history",
    response_model=List[HistoryItem],
    summary="直近の監査履歴を取得",
)
def get_history(limit: int = 20) -> List[HistoryItem]:
    db = _get_db()
    rows = db.list_recent(limit=limit)
    return [
        HistoryItem(
            id=r["id"],
            question=r["question"],
            response=r["response"],
            por=round(r["por"], 4),
            delta_e=round(r["delta_e"], 4),
            meaning_drift=r["meaning_drift"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
