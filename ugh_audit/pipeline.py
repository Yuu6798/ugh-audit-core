"""
ugh_audit/pipeline.py
共有パイプライン — detect → calculate → grv → mcg → advisory を一元化

server.py (REST) と mcp_server.py (MCP) で重複していた ~300 行の監査
パイプライン本体を single source of truth に統合する。

責務分離:

- このモジュール (pipeline.py): 監査ロジック本体 (pure computation)
  - detect → calculate → verdict/mode → grv → mcg → advisory
  - 出力は dict (内部 _* フィールドで DB 保存材料を caller に返す)
  - DB 保存は行わない (caller の mode-aware policy に委譲)

- caller (server.py / mcp_server.py):
  - detector の import ハンドリング (_HAS_DETECTOR / _detect の所有権を保持)
    → test の monkeypatch 互換を維持するため
  - `detect_fn` パラメータで run_audit に渡す (None = detector 不可用)
  - DB 保存 + レスポンス型 (AuditResponse / AuditOutput) への変換

schema_version は本モジュールが所有する (single source of truth)。

履歴:
- 2026-04-24 初版 (PR #110 に続く長期 PR、_run_pipeline 重複解消)
"""
from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path
from typing import Callable, List, Optional

from .metadata_generator import detect_missing_metadata
from .soft_rescue import maybe_build_soft_rescue

# パイプライン A の import (server.py / mcp_server.py と同じ sys.path 挿入)
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
    reconstruct_hit_sources,
    summarize_hit_sources,
)

logger = logging.getLogger(__name__)

# --- 公開定数 ---
# 2.1.0: degraded_reason に detector_unavailable / detector_error:<type> を追加
# (additive — 既存 consumer は新しい enum 値を unknown string として無視可能)
SCHEMA_VERSION = "2.1.0"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


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


# 旧 4-引数版 (test_pipeline_a 等が直接 import している)
def _gate_verdict(f1: float, f2: float, f3: float, f4: float) -> str:
    return _gate_verdict_safe(f1, f2, f3, f4)


def _primary_fail(f1: float, f2: float, f3: float, f4: float) -> str:
    worst = max(f1, f2, f3, f4)
    if worst == 0.0:
        return "none"
    labels: dict[str, float] = {"f1": f1, "f2": f2, "f3": f3, "f4": f4}
    return max(labels, key=labels.get)


# ---------------------------------------------------------------------------
# メインパイプライン
# ---------------------------------------------------------------------------


def run_audit(
    *,
    question: str,
    response: str,
    reference: Optional[str],
    question_meta: Optional[dict],
    session_id: Optional[str],
    auto_generate_meta: bool = False,
    detect_fn: Optional[Callable[[str, str, dict], Evidence]] = None,
) -> dict:
    """監査パイプラインを実行し、全フィールドを含む dict を返す。

    Args:
        question, response, reference, question_meta, session_id: 入力。
        auto_generate_meta: True + detect_fn 利用可能 + ANTHROPIC_API_KEY 設定時、
            question_meta 未提供で LLM 生成を試みる (opt-in)。
        detect_fn: detector.detect 関数。None の場合 detector 利用不可とみなし、
            degraded_reason に detector_unavailable を付与する。

    Returns:
        dict: 出力スキーマ (schema_version 2.1.0)。
        caller は mode が computed 系のとき DB 保存を行い、
        自分の response 型 (AuditResponse / AuditOutput) に変換する。
    """
    errors: List[str] = []
    metadata_source = META_SOURCE_NONE
    matched_id: Optional[str] = None
    has_detector = detect_fn is not None

    # LLM meta 自動生成 (opt-in、detector 利用可能時のみ)
    # 部分的な inline メタデータが提供された場合は欠損フィールドのみ補完する
    missing_fields = detect_missing_metadata(question_meta)
    if missing_fields and auto_generate_meta and has_detector:
        try:
            from experiments.meta_generator import generate_meta
            generated = generate_meta(question)
            actually_filled = any(
                fld in generated and _is_field_filled(generated[fld])
                for fld in missing_fields
            )
            # core_propositions が欠損のまま残っていたら detect は無意味
            if "core_propositions" in missing_fields and not generated.get(
                "core_propositions"
            ):
                actually_filled = False
            if question_meta:
                merged = dict(question_meta)
                for fld in missing_fields:
                    if fld in generated and _is_field_filled(generated[fld]):
                        merged[fld] = generated[fld]
                question_meta = merged
            else:
                if actually_filled:
                    question_meta = generated
            if actually_filled:
                if generated.get("_is_fallback"):
                    errors.append("auto_generate_fallback")
                    if "core_propositions" in missing_fields:
                        metadata_source = META_SOURCE_FALLBACK
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

    # detect → calculate
    detected = False
    detector_failed = False
    if question_meta and has_detector:
        if metadata_source not in (META_SOURCE_LLM, META_SOURCE_FALLBACK):
            metadata_source = META_SOURCE_INLINE
        question_id = question_meta.get("id", "unknown")
        matched_id = question_id
        if "question" not in question_meta:
            question_meta = {**question_meta, "question": question}
        try:
            evidence = detect_fn(question_id, response, question_meta)
            detected = True
        except Exception as exc:
            logger.exception("detector raised for question_id=%s", question_id)
            evidence = Evidence(question_id=question_id, f4_premise=None)
            errors.append(f"detector_error:{type(exc).__name__}")
            detector_failed = True
    else:
        question_id = (
            question_meta.get("id", "unknown") if question_meta else "unknown"
        )
        evidence = Evidence(question_id=question_id, f4_premise=None)
        if not question_meta:
            errors.append("question_meta_missing")
        elif not has_detector:
            errors.append("detector_unavailable")

    state = calculate(evidence)

    computed: List[str] = ["S"]
    missing: List[str] = []

    if detected:
        computed.extend(["f1", "f2", "f3"])
        if evidence.f4_premise is not None:
            computed.append("f4")
        else:
            missing.append("f4")
            errors.append("f4_trap_type_missing")
    else:
        missing.extend(["f1", "f2", "f3", "f4"])
        # detector_failed 時は detector_error:<type> がすでに degraded_reason に
        # 載っているため、detection_skipped は付けない
        if not detector_failed:
            errors.append("detection_skipped")

    if state.C is not None:
        computed.append("C")
    else:
        missing.append("C")
        # detector_failed 時は core_propositions は実際には提供されていた
        if "question_meta_missing" not in errors and not detector_failed:
            errors.append("core_propositions_missing")

    # verdict / mode
    verdict = derive_verdict(state)
    mode = derive_mode(state, metadata_source=metadata_source)
    if mode in ("computed", "computed_ai_draft"):
        computed.extend(["delta_e", "quality_score"])
    else:
        missing.extend(["delta_e", "quality_score"])

    # フォールバック meta は degraded に強制 (analytics 汚染を防止)
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
    except Exception:
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
        "advisory_flags": list(advisory_flags),
        "hit_sources": summarize_hit_sources(
            reconstruct_hit_sources(evidence),
            evidence.propositions_total,
        ),
        # DB 保存用メタデータ (caller が DB 保存時に使う)
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
