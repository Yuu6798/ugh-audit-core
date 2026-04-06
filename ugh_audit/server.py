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

import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .mcp_server import configure as _mcp_configure
from .mcp_server import mcp as _mcp_instance
from .reference.golden_store import GoldenStore
from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import Evidence, calculate  # noqa: E402

# detector は question_meta がある場合のみ使用
try:
    from detector import detect as _detect  # noqa: E402
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False

# --- 定数 ---
SCHEMA_VERSION = "2.0.0"

_VERDICT_ACCEPT = 0.10
_VERDICT_REWRITE = 0.25


def _verdict(delta_e: float) -> str:
    if delta_e <= _VERDICT_ACCEPT:
        return "accept"
    if delta_e <= _VERDICT_REWRITE:
        return "rewrite"
    return "regenerate"


def _gate_verdict_safe(f1: float, f2: float, f3: float, f4: Optional[float]) -> str:
    vals = [f1, f2, f3] + ([f4] if f4 is not None else [])
    fail_max = max(vals) if vals else 0.0
    if fail_max == 0.0:
        return "pass"
    if fail_max >= 1.0:
        return "fail"
    return "warn"


def _primary_fail_safe(
    f1: float, f2: float, f3: float, f4: Optional[float],
) -> str:
    labels: dict[str, float] = {"f1": f1, "f2": f2, "f3": f3}
    if f4 is not None:
        labels["f4"] = f4
    worst = max(labels.values()) if labels else 0.0
    if worst == 0.0:
        return "none"
    return max(labels, key=labels.get)


# 後方互換ラッパー（テストから参照される）
def _gate_verdict(f1: float, f2: float, f3: float, f4: float) -> str:
    return _gate_verdict_safe(f1, f2, f3, f4)


def _primary_fail(f1: float, f2: float, f3: float, f4: float) -> str:
    return _primary_fail_safe(f1, f2, f3, f4)


def _run_pipeline(
    question: str,
    response: str,
    reference: Optional[str],
    question_meta: Optional[dict],
    session_id: Optional[str],
) -> dict:
    """パイプライン A を実行し、mode 付きの出力 dict を返す"""
    errors: List[str] = []
    metadata_source = "none"
    matched_id: Optional[str] = None

    if question_meta and _HAS_DETECTOR:
        metadata_source = "inline"
        question_id = question_meta.get("id", "unknown")
        matched_id = question_id
        if "question" not in question_meta:
            question_meta = {**question_meta, "question": question}
        evidence = _detect(question_id, response, question_meta)
    else:
        evidence = Evidence(question_id="unknown", f4_premise=None)
        if not question_meta:
            errors.append("question_meta_missing")

    state = calculate(evidence)

    # computed_components / missing_components
    computed: List[str] = ["S"]
    missing: List[str] = []

    # f1-f3 は常に計算
    computed.extend(["f1", "f2", "f3"])

    if evidence.f4_premise is not None:
        computed.append("f4")
    else:
        missing.append("f4")
        errors.append("f4_trap_type_missing")

    if state.C is not None:
        computed.append("C")
    else:
        missing.append("C")
        if "question_meta_missing" not in errors:
            errors.append("core_propositions_missing")

    # verdict / mode
    if state.C is not None and state.delta_e is not None:
        verdict = _verdict(state.delta_e)
        mode = "computed"
        computed.extend(["delta_e", "quality_score"])
    else:
        verdict = "degraded"
        mode = "degraded"
        missing.extend(["delta_e", "quality_score"])

    hit_rate: Optional[str] = None
    if evidence.propositions_total > 0:
        hit_rate = f"{evidence.propositions_hit}/{evidence.propositions_total}"

    return {
        "schema_version": SCHEMA_VERSION,
        "S": state.S,
        "C": state.C,
        "delta_e": state.delta_e,
        "quality_score": state.quality_score,
        "verdict": verdict,
        "hit_rate": hit_rate,
        "structural_gate": {
            "f1": evidence.f1_anchor,
            "f2": evidence.f2_unknown,
            "f3": evidence.f3_operator,
            "f4": evidence.f4_premise,
            "gate_verdict": _gate_verdict_safe(
                evidence.f1_anchor, evidence.f2_unknown,
                evidence.f3_operator, evidence.f4_premise,
            ),
            "primary_fail": _primary_fail_safe(
                evidence.f1_anchor, evidence.f2_unknown,
                evidence.f3_operator, evidence.f4_premise,
            ),
        },
        "mode": mode,
        "matched_id": matched_id,
        "metadata_source": metadata_source,
        "computed_components": sorted(computed),
        "missing_components": sorted(missing),
        "errors": errors,
        "degraded_reason": errors if mode != "computed" else [],
        # DB 保存用メタデータ
        "_session_id": session_id or str(uuid.uuid4()),
        "_question": question,
        "_response": response,
        "_reference": reference,
    }


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
    mode: str = Field(..., description="実行モード: computed / degraded / error")
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
    created_at: str


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

_db: Optional[AuditDB] = None
_golden: Optional[GoldenStore] = None


def _get_db() -> AuditDB:
    global _db
    if _db is None:
        import os
        db_path = os.environ.get("UGH_AUDIT_DB")
        _db = AuditDB(db_path=Path(db_path) if db_path else None)
    return _db


def _get_golden() -> GoldenStore:
    global _golden
    if _golden is None:
        _golden = GoldenStore()
    return _golden


# ---------------------------------------------------------------------------
# テスト用: DB/GoldenStore を差し替える
# ---------------------------------------------------------------------------


def configure(
    db: Optional[AuditDB] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """テストやカスタム設定用にグローバルインスタンスを差し替える"""
    global _db, _golden
    if db is not None:
        _db = db
    if golden is not None:
        _golden = golden
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
    golden = _get_golden()
    ref = req.reference or golden.find_reference(req.question)

    result = _run_pipeline(
        question=req.question,
        response=req.response,
        reference=ref,
        question_meta=req.question_meta,
        session_id=req.session_id,
    )

    # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
    saved_id: Optional[int] = None
    if result["mode"] == "computed":
        saved_id = db.save(
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
        )

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
        mode=result["mode"],
        matched_id=result["matched_id"],
        metadata_source=result["metadata_source"],
        computed_components=result["computed_components"],
        missing_components=result["missing_components"],
        errors=result["errors"],
        degraded_reason=result["degraded_reason"],
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
            S=round(r["S"], 4),
            C=round(r["C"], 4) if r["C"] is not None else None,
            delta_e=round(r["delta_e"], 4) if r["delta_e"] is not None else None,
            quality_score=round(r["quality_score"], 4) if r["quality_score"] is not None else None,
            verdict=r["verdict"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
