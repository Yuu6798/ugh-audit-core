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
import uuid
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import dependencies as _deps
from .mcp_server import configure as _mcp_configure
from .mcp_server import mcp as _mcp_instance
from .metadata_generator import detect_missing_metadata
from .soft_rescue import maybe_build_soft_rescue

if TYPE_CHECKING:
    from .reference.golden_store import GoldenStore
    from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import (  # noqa: E402
    Evidence,
    GATE_FAIL,
    META_SOURCE_FALLBACK,
    META_SOURCE_INLINE,
    META_SOURCE_LLM,
    META_SOURCE_NONE,
    VALID_MODES,
    VALID_VERDICTS,
    calculate,
    derive_mode,
    derive_verdict,
    summarize_hit_sources,
)

# detector は question_meta がある場合のみ使用
try:
    from detector import detect as _detect  # noqa: E402
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False

logger = logging.getLogger(__name__)

# --- 定数 ---
SCHEMA_VERSION = "2.0.0"


def _is_field_filled(value: object) -> bool:
    """自動生成フィールドが有意な値を持つか判定する。

    リスト型は空でないこと、それ以外は None でないことを要求する。
    trap_type="" は「罠なし」の明示指定として有意。
    """
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    return True


def _gate_verdict_safe(f1: float, f2: float, f3: float, f4: Optional[float]) -> str:
    vals = [f1, f2, f3] + ([f4] if f4 is not None else [])
    fail_max = max(vals) if vals else 0.0
    if fail_max >= 1.0:
        return GATE_FAIL
    if f4 is None:
        return "incomplete"
    if fail_max == 0.0:
        return "pass"
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
    auto_generate_meta: bool = False,
) -> dict:
    """パイプライン A を実行し、mode 付きの出力 dict を返す

    auto_generate_meta=True の場合、question_meta 未提供時に
    LLM (Claude API) で動的生成を試みる。
    """
    errors: List[str] = []
    metadata_source = META_SOURCE_NONE
    matched_id: Optional[str] = None

    # LLM meta 自動生成（opt-in、detector 利用可能時のみ）
    # 部分的な inline メタデータが提供された場合は欠損フィールドのみ補完する
    missing_fields = detect_missing_metadata(question_meta)
    if missing_fields and auto_generate_meta and _HAS_DETECTOR:
        try:
            from experiments.meta_generator import generate_meta
            generated = generate_meta(question)
            # 空リストは unfilled、空文字列は filled（trap_type="" は「罠なし」の明示指定）
            actually_filled = any(
                field in generated and _is_field_filled(generated[field])
                for field in missing_fields
            )
            # core_propositions が欠損のまま残っていたら detect は無意味
            if "core_propositions" in missing_fields and not generated.get(
                "core_propositions"
            ):
                actually_filled = False
            if question_meta:
                # inline 提供分を保持し、欠損フィールドのみ LLM 生成値で補完
                merged = dict(question_meta)
                for field in missing_fields:
                    if field in generated and _is_field_filled(generated[field]):
                        merged[field] = generated[field]
                question_meta = merged
            else:
                if actually_filled:
                    question_meta = generated
            if actually_filled:
                if generated.get("_is_fallback"):
                    errors.append("auto_generate_fallback")
                    if "core_propositions" in missing_fields:
                        # core_propositions がフォールバック由来 → degraded
                        metadata_source = META_SOURCE_FALLBACK
                    # else: optional フィールドのみ補完 → inline として扱う
                else:
                    metadata_source = META_SOURCE_LLM
            else:
                logger.warning(
                    "auto meta generation returned empty values for %s", missing_fields
                )
                errors.append("auto_generate_empty")
        except Exception:
            logger.exception("auto meta generation failed")
            errors.append("auto_generate_failed")

    detected = False
    if question_meta and _HAS_DETECTOR:
        if metadata_source not in (META_SOURCE_LLM, META_SOURCE_FALLBACK):
            metadata_source = META_SOURCE_INLINE
        question_id = question_meta.get("id", "unknown")
        matched_id = question_id
        if "question" not in question_meta:
            question_meta = {**question_meta, "question": question}
        evidence = _detect(question_id, response, question_meta)
        detected = True
    else:
        question_id = (
            question_meta.get("id", "unknown") if question_meta else "unknown"
        )
        evidence = Evidence(question_id=question_id, f4_premise=None)
        if not question_meta:
            errors.append("question_meta_missing")

    state = calculate(evidence)

    # computed_components / missing_components
    computed: List[str] = ["S"]
    missing: List[str] = []

    if detected:
        # detect() が実行された場合のみ f1-f3 を computed とする
        computed.extend(["f1", "f2", "f3"])
        if evidence.f4_premise is not None:
            computed.append("f4")
        else:
            missing.append("f4")
            errors.append("f4_trap_type_missing")
    else:
        # detect() 未実行: f1-f4 は全て未計算（デフォルト値であり検出結果ではない）
        missing.extend(["f1", "f2", "f3", "f4"])
        errors.append("detection_skipped")

    if state.C is not None:
        computed.append("C")
    else:
        missing.append("C")
        if "question_meta_missing" not in errors:
            errors.append("core_propositions_missing")

    # verdict / mode (集約関数で導出)
    verdict = derive_verdict(state)
    mode = derive_mode(state, metadata_source=metadata_source)
    if mode in ("computed", "computed_ai_draft"):
        computed.extend(["delta_e", "quality_score"])
    else:
        missing.extend(["delta_e", "quality_score"])

    # フォールバック meta は degraded に強制（analytics 汚染を防止）
    # computed_components/missing_components も degraded 契約に合わせる
    if metadata_source == META_SOURCE_FALLBACK:
        mode = "degraded"
        verdict = "degraded"
        for fld in ("C", "delta_e", "quality_score"):
            if fld in computed:
                computed.remove(fld)
            if fld not in missing:
                missing.append(fld)

    # fail-closed: verdict/mode が想定値であることを保証
    assert verdict in VALID_VERDICTS, f"invalid verdict: {verdict}"
    assert mode in VALID_MODES, f"invalid mode: {mode}"

    hit_rate: Optional[str] = None
    if evidence.propositions_total > 0:
        hit_rate = f"{evidence.propositions_hit}/{evidence.propositions_total}"

    # soft rescue (AI 草案メタデータで C=0 のとき部分ヒットを回収)
    raw_confidence = (question_meta or {}).get("metadata_confidence")
    try:
        metadata_confidence = float(raw_confidence) if raw_confidence is not None else None
    except (TypeError, ValueError):
        metadata_confidence = None
    rescue = maybe_build_soft_rescue(
        question=question,
        response=response,
        question_meta=question_meta,
        mode=mode,
        metadata_confidence=metadata_confidence,
        S=state.S,
        C=state.C,
        f2=evidence.f2_unknown,
        f3=evidence.f3_operator,
    )

    gate_v = _gate_verdict_safe(
        evidence.f1_anchor, evidence.f2_unknown,
        evidence.f3_operator, evidence.f4_premise,
    )
    is_reliable = (
        mode in ("computed", "computed_ai_draft")
        and verdict in {"accept", "rewrite", "regenerate"}
        and gate_v != GATE_FAIL
        and metadata_source != META_SOURCE_FALLBACK
    )

    # grv 計算 (SBert 依存、未導入時は None)
    try:
        from grv_calculator import compute_grv
        grv_result = compute_grv(
            question=question,
            response_text=response,
            question_meta=question_meta,
            metadata_source=metadata_source,
            c_normalized=state.C,
        )
    except Exception:  # grv は補助計測器 — 失敗時は null フォールバック
        grv_result = None

    grv_output: Optional[dict] = None
    if grv_result is not None:
        grv_output = {
            "grv": grv_result.grv,
            "grv_tag_provisional": grv_result.grv_tag,
            "grv_components": {
                "drift": grv_result.drift,
                "dispersion": grv_result.dispersion,
                "collapse_v2": grv_result.collapse_v2,
            },
            "cover_soft": grv_result.cover_soft,
            "wash_index": grv_result.wash_index,
            "wash_index_c": grv_result.wash_index_c,
            "grv_meta": {
                "n_sentences": grv_result.n_sentences,
                "n_propositions": grv_result.n_propositions,
                "collapse_v2_applicable": grv_result.collapse_v2_applicable,
                "meta_source": grv_result.meta_source,
                "ref_confidence": grv_result.ref_confidence,
                "embedding_backend": "paraphrase-multilingual-MiniLM-L12-v2",
                "grv_version": "v1.4",
                "weights": grv_result.weights,
            },
            "grv_debug": {
                "prop_affinity_per_sentence": grv_result.prop_affinity_per_sentence,
                "cover_soft_per_proposition": grv_result.cover_soft_per_proposition,
                "drift_raw_cosine": grv_result.drift_raw_cosine,
            },
        }

    # response_mode_signal (deterministic, non-binding — fails silently)
    # Lookup priority: canonical reviewed > inline explicit > not_available
    # resolved_ma is the effective mode_affordance used for scoring
    mode_signal_output: Optional[dict] = None
    resolved_ma: Optional[dict] = None
    try:
        from mode_signal import run_mode_signal
        mode_signal_output, resolved_ma = run_mode_signal(
            response_text=response,
            question_id=question_id,
            question_meta=question_meta,
            evidence_primary=evidence.mode_affordance_primary,
            evidence_secondary=evidence.mode_affordance_secondary,
            evidence_closure=evidence.mode_affordance_closure,
            evidence_action_required=evidence.mode_affordance_action_required,
        )
    except Exception:
        mode_signal_output = None

    # mode_conditioned_grv (grv + mode_affordance → 4成分解釈ベクトル)
    mcg_output: Optional[dict] = None
    mcg_obj = None
    if grv_result is not None and resolved_ma and resolved_ma.get("primary"):
        try:
            from mode_grv import compute_mode_conditioned_grv
            mcg_obj = compute_mode_conditioned_grv(
                grv_result=grv_result,
                response_text=response,
                mode_affordance_primary=resolved_ma["primary"],
                action_required=resolved_ma.get("action_required", False),
            )
            if mcg_obj is not None:
                mcg_output = {
                    "anchor_alignment": mcg_obj.anchor_alignment,
                    "balance": mcg_obj.balance,
                    "boilerplate_risk": mcg_obj.boilerplate_risk,
                    "collapse_risk": mcg_obj.collapse_risk,
                    "mode": mcg_obj.mode,
                    "focus_components": mcg_obj.focus_components,
                    "grv_raw": mcg_obj.grv_raw,
                    "version": mcg_obj.version,
                }
        except Exception:
            mcg_output = None
            mcg_obj = None

    # Phase E verdict advisory (downgrade-only, accept -> rewrite)
    # 設計: docs/phase_e_verdict_integration.md
    try:
        from mode_grv import derive_verdict_advisory
        verdict_advisory, advisory_flags = derive_verdict_advisory(verdict, mcg_obj)
    except Exception:
        verdict_advisory = verdict
        advisory_flags = []

    result = {
        "schema_version": SCHEMA_VERSION,
        "S": state.S,
        "C": None if metadata_source == META_SOURCE_FALLBACK else state.C,
        "delta_e": None if metadata_source == META_SOURCE_FALLBACK else state.delta_e,
        "quality_score": None if metadata_source == META_SOURCE_FALLBACK else state.quality_score,
        "verdict": verdict,
        "hit_rate": hit_rate,
        "structural_gate": {
            "f1": evidence.f1_anchor,
            "f2": evidence.f2_unknown,
            "f3": evidence.f3_operator,
            "f4": evidence.f4_premise,
            "gate_verdict": gate_v,
            "primary_fail": _primary_fail_safe(
                evidence.f1_anchor, evidence.f2_unknown,
                evidence.f3_operator, evidence.f4_premise,
            ),
        },
        "mode_affordance": resolved_ma,
        "mode": mode,
        "is_reliable": is_reliable,
        "matched_id": matched_id,
        "metadata_source": metadata_source,
        "computed_components": sorted(computed),
        "missing_components": sorted(missing),
        "errors": errors,
        "degraded_reason": errors if mode == "degraded" else [],
        "grv": grv_output,
        "response_mode_signal": mode_signal_output,
        "mode_conditioned_grv": mcg_output,
        "verdict_advisory": verdict_advisory,
        "advisory_flags": advisory_flags,
        # Phase 1 paper-defense: hit_sources 構造化サマリ。core vs cascade の
        # 分離を API 出力に surfacing する（詳細は docs/validation.md）。
        "hit_sources": summarize_hit_sources(
            evidence.hit_sources if hasattr(evidence, "hit_sources") else {},
            evidence.propositions_total,
        ),
        # DB 保存用メタデータ
        "_session_id": session_id or str(uuid.uuid4()),
        "_question": question,
        "_response": response,
        "_reference": reference,
        "_question_meta": question_meta,
        "_hit_sources": evidence.hit_sources if hasattr(evidence, "hit_sources") else {},
    }
    if rescue is not None:
        result["soft_rescue"] = rescue
    return result


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
