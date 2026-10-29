"""
ugh_audit/server.py
REST API + MCP サーバー（パイプライン A 対応）

FastAPI ベースの HTTP API と MCP (Model Context Protocol) サーバーを提供する。
招待制Bearer認証を設定できるMCPクライアントから /mcp/ を利用する。

主要エンドポイント:
    POST /api/audit   — REST: 監査 (question/response/question_meta → スコア)
    GET  /api/history  — REST: 直近の監査履歴
    POST /mcp          — MCP: Streamable HTTP エンドポイント
"""
from __future__ import annotations

import json as _json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import dependencies as _deps
from .beta_access import (
    AccessError, InviteMiddleware, audit_access, error_response, require_principal, validate_payload,
)
from . import pipeline as _pipeline
from .semantic_matching import DECISION_SCOPE, METRICS_SCOPE
from .mcp_server import configure as _mcp_configure
from .mcp_server import mcp as _mcp_instance

# 後方互換: 旧実装で server.py に直接定義されていた constants / helpers を
# pipeline.py から re-export する (test_pipeline_a 等が直接 import する)
from .pipeline import (
    SCHEMA_VERSION,  # noqa: F401
    _gate_verdict,  # noqa: F401
    _gate_verdict_safe,  # noqa: F401
    _primary_fail,  # noqa: F401
    _primary_fail_safe,  # noqa: F401
)

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


def _make_semantic_judge():
    """Build the server-owned judge; request metadata cannot disable it."""
    from .semantic_matching import make_judge_from_env

    return make_judge_from_env()


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
        semantic_judge=_make_semantic_judge(),
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
            "要件がない・不正な場合に LLM で生成する (opt-in)。"
            "成功した要件草案は metadata_source='llm_generated'、"
            "未検証の代替メタデータは 'fallback' として degraded にする。"
            "ANTHROPIC_API_KEY 環境変数が必要。"
        ),
    )
    retry_of: Optional[int] = Field(
        None,
        description="再監査元の saved_id。初回監査では省略。",
    )


class StructuralGateResponse(BaseModel):
    f1: Optional[float] = None
    f2: Optional[float] = None
    f3: Optional[float] = None
    f4: Optional[float] = None
    gate_verdict: str
    primary_fail: str
    binding: bool = False


class AuditResponse(BaseModel):
    """audit_answer ツールの出力"""

    schema_version: str = Field(..., description="レスポンススキーマのバージョン")
    decision_scope: str = Field(..., description="判定対象の固定契約")
    metrics_scope: str = Field(..., description="数値の意味と利用範囲")
    assessment_context: Optional[dict] = Field(None, description="照合に用いた要件メタデータ")
    S: Optional[float] = Field(None, description="未校正のため null。参考値は legacy_metrics")
    C: Optional[float] = Field(None, description="検証済み要件の充足率 (0–1) / 保留時 null")
    delta_e: Optional[float] = Field(None, description="未校正のため null")
    quality_score: Optional[float] = Field(
        None, description="未校正のため null"
    )
    verdict: str = Field(
        ..., description="要件充足の判定: accept / rewrite / degraded"
    )
    semantic_matching: dict = Field(..., description="v2の要件別照合と根拠・由来")
    requirement_verdict: str = Field(..., description="complete / incomplete / undetermined")
    requires_review: bool = Field(..., description="欠落・保留・AI草案要件による要確認")
    legacy_metrics: dict = Field(..., description="最終判定に影響しない旧診断値")
    hit_rate: Optional[str] = Field(None, description="要件充足比率 (例: '3/5') / 保留時 null")
    structural_gate: Optional[StructuralGateResponse] = None
    saved_id: Optional[int] = Field(None, description="DB保存時の行ID (degraded時はnull)")
    retry_of: Optional[int] = Field(None, description="再監査元の saved_id (初回はnull)")
    mode: str = Field(..., description="実行モード: computed / computed_ai_draft / degraded")
    is_reliable: bool = Field(
        ..., description="全要件の照合が確定した場合 true。回答の真実性や承認は意味しない"
    )
    matched_id: Optional[str] = Field(None, description="対応づけた question_id")
    metadata_source: str = Field(
        ..., description="メタデータ源: inline / llm_generated / fallback / none"
    )
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
        None, description="旧 soft-hit rescue の互換フィールド。schema 4 では null"
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
        description="要件照合による判定。従来 advisory は legacy_metrics に保存。",
    )
    advisory_flags: List[str] = Field(
        default_factory=list,
        description="schema 4 では空配列。旧 advisory は legacy_metrics に保存。",
    )
    hit_sources: Optional[dict] = Field(
        None,
        description="検証済み要件照合の fulfilled / missing / unknown 件数と要件別結果。",
    )


class HistoryItem(BaseModel):
    """履歴1件"""

    id: int
    question: str
    response: str
    schema_version: str = "4.0.0"
    source_schema_version: str = ""
    decision_scope: str = DECISION_SCOPE
    metrics_scope: str = METRICS_SCOPE
    assessment_context: Optional[dict] = None
    S: Optional[float] = None
    C: Optional[float] = None
    delta_e: Optional[float] = None
    quality_score: Optional[float] = None
    verdict: str
    semantic_matching: Optional[dict] = None
    requirement_verdict: str = "undetermined"
    requires_review: bool = True
    legacy_metrics: Optional[dict] = None
    mode: str = "degraded"
    is_reliable: bool = False
    structural_gate: Optional[dict] = None
    f1: Optional[float] = None
    f2: Optional[float] = None
    f3: Optional[float] = None
    f4: Optional[float] = None
    hit_rate: Optional[str] = None
    metadata_source: str = "inline"
    generated_meta: str = ""
    hit_sources: Optional[dict] = None
    retry_of: Optional[int] = None
    created_at: str


class SessionSummary(BaseModel):
    """セッション集計"""

    session_id: str
    schema_version: str = "4.0.0"
    decision_scope: str = DECISION_SCOPE
    metrics_scope: str = METRICS_SCOPE
    total: int
    verified_total: int = Field(
        0, description="有効な照合結果の保存件数。欠落確認も含み、合格件数ではない。",
    )
    unverified_total: int = Field(0, description="旧記録・保留等で現在の指標を使えない件数")
    avg_delta_e: Optional[float] = None
    avg_S: Optional[float] = None
    min_delta_e: Optional[float] = None
    max_delta_e: Optional[float] = None
    avg_quality_score: Optional[float] = None
    legacy_summary: Optional[dict] = None


class DriftItem(BaseModel):
    """要件充足率・判定の時系列1件"""

    created_at: str
    schema_version: str = "4.0.0"
    source_schema_version: str = ""
    decision_scope: str = DECISION_SCOPE
    metrics_scope: str = METRICS_SCOPE
    assessment_context: Optional[dict] = None
    response: str = ""
    mode: str = "degraded"
    is_reliable: bool = False
    structural_gate: Optional[dict] = None
    metadata_source: str = "none"
    hit_rate: Optional[str] = None
    S: Optional[float] = None
    C: Optional[float] = None
    delta_e: Optional[float] = None
    quality_score: Optional[float] = None
    verdict: str
    semantic_matching: Optional[dict] = None
    requirement_verdict: str = "undetermined"
    requires_review: bool = True
    legacy_metrics: Optional[dict] = None


# ---------------------------------------------------------------------------
# MCP サーバー準備 + ライフスパン
# ---------------------------------------------------------------------------

_mcp_http_app = _mcp_instance.streamable_http_app()


@asynccontextmanager
async def _lifespan(a: FastAPI):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings

    security = TransportSecuritySettings(
        allowed_hosts=["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]
        + [v.strip() for v in os.environ.get("UGH_ALLOWED_HOSTS", "").split(",") if v.strip()],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"]
        + [v.strip() for v in os.environ.get("UGH_ALLOWED_ORIGINS", "").split(",") if v.strip()],
    )

    sm = StreamableHTTPSessionManager(
        app=_mcp_instance._mcp_server,
        json_response=_mcp_instance.settings.json_response,
        stateless=_mcp_instance.settings.stateless_http,
        security_settings=security,
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
        "明示された要件ごとの充足・欠落・判定保留を根拠付きで照合する。"
        "C は要件充足率、従来の構造指標と GRV は参考診断。"
        "\n\nMCP エンドポイント: POST /mcp"
    ),
    version="0.4.0",
    lifespan=_lifespan,
)

# Preflight contains no audit data. Keep CORS outside auth so browsers can send
# the real authenticated request; all non-preflight requests still pass auth.
app.add_middleware(InviteMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "https://chatgpt.com"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)

@app.exception_handler(AccessError)
async def access_error_handler(request, exc):
    return error_response(exc)


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
def audit_answer(req: AuditRequest) -> AuditResponse:
    db = _get_db()
    validate_payload(req.question, req.response, req.reference, req.session_id, req.question_meta)
    with audit_access(db, req.retry_of) as principal:
        golden = _get_golden()
        ref = req.reference or golden.find_reference(req.question)

        # The whole synchronous endpoint runs in a cancellation-shielded worker.
        result = _run_pipeline(
            question=req.question,
            response=req.response,
            reference=ref,
            question_meta=req.question_meta,
            session_id=req.session_id,
            auto_generate_meta=req.auto_generate_meta,
        )

        # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
        saved_id: Optional[int] = None
        if result["mode"] in ("computed", "computed_ai_draft"):
            saved_id = db.save(
                owner_id=principal.owner_id,
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
                f4=result["structural_gate"]["f4"],
                hit_rate=result["hit_rate"] or "",
                metadata_source=result["metadata_source"],
                generated_meta=_json.dumps(
                    result.get("_question_meta") or {}, ensure_ascii=False,
                ) if result.get("metadata_source") == META_SOURCE_LLM else "",
                hit_sources=_json.dumps(result.get("hit_sources"), ensure_ascii=False),
                retry_of=req.retry_of,
                semantic_matching=result["semantic_matching"],
                requirement_verdict=result["requirement_verdict"],
                requires_review=result["requires_review"],
                legacy_metrics=result["legacy_metrics"],
                schema_version=result["schema_version"],
                decision_scope=result["decision_scope"],
                metrics_scope=result["metrics_scope"],
                question_meta=result.get("_question_meta"),
                structural_gate=result.get("structural_gate"),
                mode=result["mode"],
                is_reliable=result["is_reliable"],
            )

        return AuditResponse(
            schema_version=result["schema_version"],
            decision_scope=result["decision_scope"],
            metrics_scope=result["metrics_scope"],
            assessment_context=result.get("_question_meta"),
            S=result["S"],
            C=result["C"],
            delta_e=result["delta_e"],
            quality_score=result["quality_score"],
            verdict=result["verdict"],
            semantic_matching=result["semantic_matching"],
            requirement_verdict=result["requirement_verdict"],
            requires_review=result["requires_review"],
            legacy_metrics=result["legacy_metrics"],
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
    rows = db.list_recent(limit=max(1, min(limit, 500)), owner_id=require_principal().owner_id)
    return [_row_to_history(r) for r in rows]


def _row_to_history(r: dict) -> HistoryItem:
    from .storage.audit_db import guard_history_row

    r = guard_history_row(r)
    gate = r.get("structural_gate") or {}
    return HistoryItem(
        id=r["id"],
        question=r["question"],
        response=r["response"],
        S=round(r["S"], 4) if r["S"] is not None else None,
        schema_version=r["schema_version"],
        source_schema_version=r.get("source_schema_version", ""),
        decision_scope=r["decision_scope"],
        metrics_scope=r["metrics_scope"],
        assessment_context=r.get("assessment_context"),
        mode=r["mode"],
        is_reliable=r["is_reliable"],
        structural_gate=r.get("structural_gate"),
        C=r["C"],
        delta_e=round(r["delta_e"], 4) if r["delta_e"] is not None else None,
        quality_score=round(r["quality_score"], 4) if r["quality_score"] is not None else None,
        verdict=r["verdict"],
        semantic_matching=r.get("semantic_matching"),
        requirement_verdict=r.get("requirement_verdict", "undetermined"),
        requires_review=r.get("requires_review", True),
        legacy_metrics=r.get("legacy_metrics"),
        f1=round(gate["f1"], 4) if gate.get("f1") is not None else None,
        f2=round(gate["f2"], 4) if gate.get("f2") is not None else None,
        f3=round(gate["f3"], 4) if gate.get("f3") is not None else None,
        f4=round(gate["f4"], 4) if gate.get("f4") is not None else None,
        hit_rate=r.get("hit_rate", ""),
        metadata_source=r.get("metadata_source", "inline"),
        generated_meta=r.get("generated_meta", ""),
        hit_sources=r.get("hit_sources"),
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
    r = db.get_by_id(audit_id, owner_id=require_principal().owner_id)
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
    summary = db.session_summary(session_id, owner_id=require_principal().owner_id)
    return SessionSummary(**summary)


@app.get(
    "/api/drift",
    response_model=List[DriftItem],
    summary="要件充足率と判定の時系列",
)
def get_drift(limit: int = 100) -> List[DriftItem]:
    db = _get_db()
    rows = db.drift_history(limit=max(1, min(limit, 1000)), owner_id=require_principal().owner_id)
    return [
        DriftItem(
            created_at=r["created_at"],
            S=round(r["S"], 4) if r["S"] is not None else None,
            schema_version=r["schema_version"],
            source_schema_version=r.get("source_schema_version", ""),
            decision_scope=r["decision_scope"],
            metrics_scope=r["metrics_scope"],
            assessment_context=r.get("assessment_context"),
            response=r.get("response", ""),
            mode=r["mode"],
            is_reliable=r["is_reliable"],
            structural_gate=r.get("structural_gate"),
            metadata_source=r.get("metadata_source", "none"),
            hit_rate=r.get("hit_rate"),
            C=r["C"],
            delta_e=round(r["delta_e"], 4) if r["delta_e"] is not None else None,
            quality_score=round(r["quality_score"], 4) if r["quality_score"] is not None else None,
            verdict=r["verdict"],
            semantic_matching=r.get("semantic_matching"),
            requirement_verdict=r.get("requirement_verdict", "undetermined"),
            requires_review=r.get("requires_review", True),
            legacy_metrics=r.get("legacy_metrics"),
        )
        for r in rows
    ]


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
