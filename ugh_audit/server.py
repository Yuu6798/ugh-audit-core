"""
ugh_audit/server.py
REST API + MCP サーバー（パイプライン A 対応）

FastAPI ベースの HTTP API と MCP (Model Context Protocol) サーバーを提供する。
ChatGPT Connectors から MCP URL (http://<host>:<port>/mcp) を登録して利用可能。

主要エンドポイント:
    POST /api/audit   — REST: 監査 (question/response/question_meta → スコア)
    GET  /api/history  — REST: 直近の監査履歴
    POST /mcp          — MCP: Streamable HTTP エンドポイント
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import dependencies as _deps
from . import pipeline as _pipeline
from .mcp_server import configure as _mcp_configure
from .mcp_server import mcp as _mcp_instance

# 後方互換: 旧実装で server.py に直接定義されていた constants / helpers を
# pipeline.py から re-export する (test_pipeline_a 等が直接 import する)
from .pipeline import SCHEMA_VERSION  # noqa: F401
from .pipeline import _gate_verdict  # noqa: F401
from .pipeline import _gate_verdict_safe  # noqa: F401
from .pipeline import _primary_fail  # noqa: F401
from .pipeline import _primary_fail_safe  # noqa: F401

if TYPE_CHECKING:
    from .reference.golden_store import GoldenStore
    from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import (  # noqa: E402
    META_SOURCE_LLM,
)

# detector は question_meta がある場合のみ使用。
# _HAS_DETECTOR / _detect は test の monkeypatch 互換のため module level に残す。
# 実際の監査ロジックは pipeline.run_audit() が担い、本モジュールは detect_fn を
# 渡す thin wrapper として機能する。
try:
    from detector import detect as _detect  # noqa: E402
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False
    _detect = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _run_pipeline(
    question: str,
    response: str,
    reference: Optional[str],
    question_meta: Optional[dict],
    session_id: Optional[str],
    auto_generate_meta: bool = False,
) -> dict:
    """パイプラインを実行し、mode 付きの出力 dict を返す (thin wrapper)。

    実際の監査ロジックは :func:`ugh_audit.pipeline.run_audit` が担う。
    本関数は server module level の ``_HAS_DETECTOR`` / ``_detect`` を
    ``detect_fn`` に束ねて渡す役割のみ。test が monkeypatch で両属性を
    差し替えた場合、その差し替え後の値が毎回の呼び出しで参照される。
    """
    return _pipeline.run_audit(
        question=question,
        response=response,
        reference=reference,
        question_meta=question_meta,
        session_id=session_id,
        auto_generate_meta=auto_generate_meta,
        detect_fn=_detect if _HAS_DETECTOR else None,
    )


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
    session_id: Optional[str] = Field(
        None, description="セッションID（省略時は空文字列）"
    )
    question_meta: Optional[dict] = Field(
        None, description="問題メタデータ（core_propositions 等を含む dict）"
    )
    auto_generate_meta: bool = Field(
        False,
        description=(
            "question_meta 未提供時に LLM で動的生成する (opt-in)。"
            "metadata_source='llm_generated' として結果に明示される。"
            "ANTHROPIC_API_KEY 環境変数が必要。"
        ),
    )
    retry_of: Optional[int] = Field(
        None,
        description="再監査元の saved_id。初回監査では省略。",
    )


class StructuralGateResponse(BaseModel):
    f1: float
    f2: float
    f3: float
    f4: Optional[float] = None
    gate_verdict: str
    primary_fail: str


class AuditResponse(BaseModel):
    """audit_answer ツールの出力"""

    schema_version: str = Field(..., description="レスポンススキーマのバージョン")
    S: float = Field(..., description="構造完全性 (0–1)")
    C: Optional[float] = Field(None, description="命題被覆率 (0–1) / null=未計算")
    delta_e: Optional[float] = Field(None, description="ΔE — 意味距離 (0–1) / null=未計算")
    quality_score: Optional[float] = Field(
        None, description="品質スコア (1–5) / null=未計算"
    )
    verdict: str = Field(
        ..., description="判定: accept / rewrite / regenerate / degraded"
    )
    hit_rate: Optional[str] = Field(None, description="命題ヒット率 (例: '3/5')")
    structural_gate: Optional[StructuralGateResponse] = None
    saved_id: Optional[int] = Field(None, description="DB保存時の行ID (degraded時はnull)")
    retry_of: Optional[int] = Field(None, description="再監査元の saved_id (初回はnull)")
    mode: str = Field(..., description="実行モード: computed / computed_ai_draft / degraded")
    is_reliable: bool = Field(
        ..., description="結果が信頼できるか (mode=computed かつ verdict が計算済みの場合 true)"
    )
    matched_id: Optional[str] = Field(None, description="対応づけた question_id")
    metadata_source: str = Field(..., description="メタデータ源: inline / golden_store / none")
    computed_components: List[str] = Field(
        default_factory=list, description="計算済みコンポーネントのリスト"
    )
    missing_components: List[str] = Field(
        default_factory=list, description="未計算コンポーネントのリスト"
    )
    errors: List[str] = Field(default_factory=list, description="エラーメッセージのリスト")
    degraded_reason: List[str] = Field(
        default_factory=list, description="degraded 時の理由リスト"
    )
    soft_rescue: Optional[dict] = Field(
        None, description="AI草案メタデータの soft-hit rescue 結果 (該当時のみ)"
    )
    mode_affordance: Optional[dict] = Field(
        None, description="質問の応答形式 {primary, secondary} (未設定時は null)"
    )
    grv: Optional[dict] = Field(
        None, description="因果構造損失 (grv) 計算結果 (SBert 未導入時は null)"
    )
    response_mode_signal: Optional[dict] = Field(
        None, description="応答モード適合度信号 (deterministic, non-binding)"
    )
    mode_conditioned_grv: Optional[dict] = Field(
        None, description="モード条件付き grv 解釈ベクトル (grv + mode_affordance)"
    )
    verdict_advisory: str = Field(
        ...,
        description=(
            "Phase E: mode_conditioned_grv を反映した advisory verdict。"
            "primary `verdict` を弱信号で downgrade するのみ (accept -> rewrite)。"
            "consumer は任意に採用する。"
        ),
    )
    advisory_flags: List[str] = Field(
        default_factory=list,
        description=(
            "Phase E: advisory downgrade 発火ルールのリスト。"
            "未知の flag は無視されるべき。"
            "例: mcg_collapse_downgrade, mcg_anchor_missing"
        ),
    )
    hit_sources: Optional[dict] = Field(
        None,
        description=(
            "Paper defense: 命題ヒットの発生源を core (tfidf) / cascade_rescued / "
            "miss に分離したサマリ。`core_only_hit_rate` は決定性主張の分子 "
            "(tfidf-only) を示す。命題未検出時は null。"
            "{core_hit, cascade_rescued, miss, total, core_only_hit_rate, per_proposition}"
        ),
    )


class HistoryItem(BaseModel):
    """履歴1件"""

    id: int
    question: str
    response: str
    S: float
    C: Optional[float] = None
    delta_e: Optional[float] = None
    quality_score: Optional[float] = None
    verdict: str
    f1: float = 0.0
    f2: float = 0.0
    f3: float = 0.0
    f4: float = 0.0
    hit_rate: str = ""
    metadata_source: str = "inline"
    generated_meta: str = ""
    hit_sources: str = ""
    retry_of: Optional[int] = None
    created_at: str


class SessionSummary(BaseModel):
    """セッション集計"""

    session_id: str
    total: int
    avg_delta_e: float
    min_delta_e: float
    max_delta_e: float
    avg_quality_score: float


class DriftItem(BaseModel):
    """ΔE時系列1件"""

    created_at: str
    S: float
    C: Optional[float] = None
    delta_e: Optional[float] = None
    quality_score: Optional[float] = None
    verdict: str


# ---------------------------------------------------------------------------
# MCP サーバー準備 + ライフスパン
# ---------------------------------------------------------------------------

_mcp_http_app = _mcp_instance.streamable_http_app()


@asynccontextmanager
async def _lifespan(a: FastAPI):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    sm = StreamableHTTPSessionManager(
        app=_mcp_instance._mcp_server,
        json_response=_mcp_instance.settings.json_response,
        stateless=_mcp_instance.settings.stateless_http,
    )
    _mcp_instance._session_manager = sm
    _mcp_http_app.routes[0].app.session_manager = sm
    async with sm.run():
        yield


# ---------------------------------------------------------------------------
# アプリケーション
# ---------------------------------------------------------------------------

app = FastAPI(
    title="UGH Audit",
    description=(
        "AI回答の意味論的監査ツール。"
        "パイプライン A (S / C / ΔE / quality_score) で意味的誠実性を定量評価する。"
        "\n\nMCP エンドポイント: POST /mcp"
    ),
    version="0.4.0",
    lifespan=_lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "https://chatgpt.com"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)

app.mount("/mcp", _mcp_http_app)

# ---------------------------------------------------------------------------
# 共有インスタンス
# ---------------------------------------------------------------------------

def _get_db() -> AuditDB:
    return _deps.get_db()


def _get_golden() -> GoldenStore:
    return _deps.get_golden()


# ---------------------------------------------------------------------------
# テスト用: DB/GoldenStore を差し替える
# ---------------------------------------------------------------------------


def configure(
    db: Optional[AuditDB] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """テストやカスタム設定用にグローバルインスタンスを差し替える"""
    if db is None and golden is None:
        _deps.reset()
    else:
        _deps.configure(db=db, golden=golden)
    _mcp_configure(db=db, golden=golden)


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@app.post(
    "/api/audit",
    response_model=AuditResponse,
    summary="audit_answer — AI回答を意味監査する",
)
async def audit_answer(req: AuditRequest) -> AuditResponse:
    db = _get_db()
    golden = _get_golden()
    ref = req.reference or golden.find_reference(req.question)

    # auto_generate_meta=True 時は LLM 呼び出しが発生するため
    # スレッドプールで実行して他のリクエストをブロックしない
    pipeline_fn = partial(
        _run_pipeline,
        question=req.question,
        response=req.response,
        reference=ref,
        question_meta=req.question_meta,
        session_id=req.session_id,
        auto_generate_meta=req.auto_generate_meta,
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, pipeline_fn)

    # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
    saved_id: Optional[int] = None
    if result["mode"] in ("computed", "computed_ai_draft"):
        save_fn = partial(
            db.save,
            session_id=result["_session_id"],
            question=result["_question"],
            response=result["_response"],
            reference=result["_reference"],
            S=result["S"],
            C=result["C"],
            delta_e=result["delta_e"],
            quality_score=result["quality_score"],
            verdict=result["verdict"],
            f1=result["structural_gate"]["f1"],
            f2=result["structural_gate"]["f2"],
            f3=result["structural_gate"]["f3"],
            f4=result["structural_gate"]["f4"] if result["structural_gate"]["f4"] is not None else 0.0,
            hit_rate=result["hit_rate"] or "",
            metadata_source=result["metadata_source"],
            generated_meta=_json.dumps(
                result.get("_question_meta") or {}, ensure_ascii=False,
            ) if result.get("metadata_source") == META_SOURCE_LLM else "",
            hit_sources=_json.dumps(
                result.get("_hit_sources", {}), ensure_ascii=False,
            ),
            retry_of=req.retry_of,
        )
        saved_id = await loop.run_in_executor(None, save_fn)

    return AuditResponse(
        schema_version=result["schema_version"],
        S=result["S"],
        C=result["C"],
        delta_e=result["delta_e"],
        quality_score=result["quality_score"],
        verdict=result["verdict"],
        hit_rate=result["hit_rate"],
        structural_gate=StructuralGateResponse(**result["structural_gate"]),
        saved_id=saved_id,
        retry_of=req.retry_of,
        mode=result["mode"],
        is_reliable=result["is_reliable"],
        matched_id=result["matched_id"],
        metadata_source=result["metadata_source"],
        computed_components=result["computed_components"],
        missing_components=result["missing_components"],
        errors=result["errors"],
        degraded_reason=result["degraded_reason"],
        mode_affordance=result.get("mode_affordance"),
        soft_rescue=result.get("soft_rescue"),
        grv=result.get("grv"),
        response_mode_signal=result.get("response_mode_signal"),
        mode_conditioned_grv=result.get("mode_conditioned_grv"),
        verdict_advisory=result.get("verdict_advisory", result["verdict"]),
        advisory_flags=result.get("advisory_flags", []),
        hit_sources=result.get("hit_sources"),
    )


@app.get(
    "/api/history",
    response_model=List[HistoryItem],
    summary="直近の監査履歴を取得",
)
def get_history(limit: int = 20) -> List[HistoryItem]:
    db = _get_db()
    rows = db.list_recent(limit=max(1, min(limit, 500)))
    return [_row_to_history(r) for r in rows]


def _row_to_history(r: dict) -> HistoryItem:
    return HistoryItem(
        id=r["id"],
        question=r["question"],
        response=r["response"],
        S=round(r["S"], 4),
        C=round(r["C"], 4) if r["C"] is not None else None,
        delta_e=round(r["delta_e"], 4) if r["delta_e"] is not None else None,
        quality_score=round(r["quality_score"], 4) if r["quality_score"] is not None else None,
        verdict=r["verdict"],
        f1=round(r.get("f1", 0.0), 4),
        f2=round(r.get("f2", 0.0), 4),
        f3=round(r.get("f3", 0.0), 4),
        f4=round(r.get("f4", 0.0), 4),
        hit_rate=r.get("hit_rate", ""),
        metadata_source=r.get("metadata_source", "inline"),
        generated_meta=r.get("generated_meta", ""),
        hit_sources=r.get("hit_sources", ""),
        retry_of=r.get("retry_of"),
        created_at=r["created_at"],
    )


@app.get(
    "/api/audit/{audit_id}",
    response_model=HistoryItem,
    summary="ID指定で監査結果を1件取得",
)
def get_audit_by_id(audit_id: int) -> HistoryItem:
    db = _get_db()
    r = db.get_by_id(audit_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"audit_id {audit_id} not found")
    return _row_to_history(r)


@app.get(
    "/api/session/{session_id}",
    response_model=SessionSummary,
    summary="セッション単位の集計サマリー",
)
def get_session(session_id: str) -> SessionSummary:
    db = _get_db()
    summary = db.session_summary(session_id)
    return SessionSummary(**summary)


@app.get(
    "/api/drift",
    response_model=List[DriftItem],
    summary="ΔE時系列データ",
)
def get_drift(limit: int = 100) -> List[DriftItem]:
    db = _get_db()
    rows = db.drift_history(limit=max(1, min(limit, 1000)))
    return [
        DriftItem(
            created_at=r["created_at"],
            S=round(r["S"], 4),
            C=round(r["C"], 4) if r["C"] is not None else None,
            delta_e=round(r["delta_e"], 4) if r["delta_e"] is not None else None,
            quality_score=round(r["quality_score"], 4) if r["quality_score"] is not None else None,
            verdict=r["verdict"],
        )
        for r in rows
    ]


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
