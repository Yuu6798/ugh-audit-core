"""
ugh_audit/mcp_server.py
MCP サーバー — ChatGPT Connectors 用（パイプライン A 対応）

Model Context Protocol (MCP) で audit_answer ツールを公開する。
ChatGPT Settings > Connectors から MCP URL を登録して利用する。

起動方法:
    python -m ugh_audit.mcp_server                 # Streamable HTTP (port 8000)
    python -m ugh_audit.mcp_server --port 9000      # ポート指定
"""
from __future__ import annotations

import json as _json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from . import dependencies as _deps
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
)

# detector（検出層）— 利用可能な場合のみ使用
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


def _primary_fail(f1: float, f2: float, f3: float, f4: float) -> str:
    worst = max(f1, f2, f3, f4)
    if worst == 0.0:
        return "none"
    labels: Dict[str, float] = {"f1": f1, "f2": f2, "f3": f3, "f4": f4}
    return max(labels, key=labels.get)


def _primary_fail_safe(
    f1: float, f2: float, f3: float, f4: Optional[float],
) -> str:
    labels: Dict[str, float] = {"f1": f1, "f2": f2, "f3": f3}
    if f4 is not None:
        labels["f4"] = f4
    worst = max(labels.values()) if labels else 0.0
    if worst == 0.0:
        return "none"
    return max(labels, key=labels.get)


# ---------------------------------------------------------------------------
# MCP サーバー定義
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "UGH Audit",
    instructions=(
        "AI回答の意味論的監査ツール。"
        "パイプライン A (S / C / ΔE / quality_score) で意味的誠実性を定量評価する。"
    ),
    streamable_http_path="/",
    stateless_http=True,
)

# ---------------------------------------------------------------------------
# 共有インスタンス（遅延初期化）
# ---------------------------------------------------------------------------

def _get_db() -> AuditDB:
    return _deps.get_db()


def _get_golden() -> GoldenStore:
    return _deps.get_golden()


def configure(
    db: Optional[AuditDB] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """テストやカスタム設定用にグローバルインスタンスを差し替える"""
    if db is None and golden is None:
        _deps.reset()
        return
    _deps.configure(db=db, golden=golden)


# ---------------------------------------------------------------------------
# 出力データクラス
# ---------------------------------------------------------------------------


@dataclass
class AuditOutput:
    """audit_answer ツールの構造化出力"""

    schema_version: str
    S: float
    C: Optional[float]
    delta_e: Optional[float]
    quality_score: Optional[float]
    verdict: str
    hit_rate: Optional[str]
    structural_gate: Dict
    saved_id: Optional[int]
    mode: str
    is_reliable: bool
    matched_id: Optional[str]
    metadata_source: str
    computed_components: List[str] = field(default_factory=list)
    missing_components: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    degraded_reason: List[str] = field(default_factory=list)
    mode_affordance: Optional[Dict] = None
    soft_rescue: Optional[Dict] = None
    grv: Optional[Dict] = None
    response_mode_signal: Optional[Dict] = None


# ---------------------------------------------------------------------------
# ツール定義
# ---------------------------------------------------------------------------


def _proxy_audit(remote_api: str, **kwargs) -> AuditOutput:
    """リモート API に監査リクエストを転送し、結果を AuditOutput に変換する"""
    import urllib.request
    import urllib.error

    url = remote_api.rstrip("/") + "/api/audit"
    payload = {k: v for k, v in kwargs.items() if v is not None}
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return AuditOutput(
            schema_version="2.0.0", S=0.0, C=None, delta_e=None,
            quality_score=None, verdict="degraded", hit_rate=None,
            structural_gate={}, saved_id=None, mode="degraded",
            is_reliable=False, matched_id=None, metadata_source="none",
            errors=[f"remote_api_error: {e.code} {body[:200]}"],
            degraded_reason=["remote_api_error"],
        )
    except Exception as e:
        return AuditOutput(
            schema_version="2.0.0", S=0.0, C=None, delta_e=None,
            quality_score=None, verdict="degraded", hit_rate=None,
            structural_gate={}, saved_id=None, mode="degraded",
            is_reliable=False, matched_id=None, metadata_source="none",
            errors=[f"remote_api_error: {e}"],
            degraded_reason=["remote_api_error"],
        )

    gate = result.get("structural_gate") or {}
    return AuditOutput(
        schema_version=result.get("schema_version", "2.0.0"),
        S=result.get("S", 0.0),
        C=result.get("C"),
        delta_e=result.get("delta_e"),
        quality_score=result.get("quality_score"),
        verdict=result.get("verdict", "degraded"),
        hit_rate=result.get("hit_rate"),
        structural_gate=gate,
        saved_id=result.get("saved_id"),
        mode=result.get("mode", "degraded"),
        is_reliable=result.get("is_reliable", False),
        matched_id=result.get("matched_id"),
        metadata_source=result.get("metadata_source", "none"),
        computed_components=result.get("computed_components", []),
        missing_components=result.get("missing_components", []),
        errors=result.get("errors", []),
        degraded_reason=result.get("degraded_reason", []),
        mode_affordance=result.get("mode_affordance"),
        soft_rescue=result.get("soft_rescue"),
        grv=result.get("grv"),
        response_mode_signal=result.get("response_mode_signal"),
    )


@mcp.tool()
def audit_answer(
    question: str,
    response: str,
    reference: Optional[str] = None,
    session_id: Optional[str] = None,
    question_meta: Optional[Dict] = None,
    auto_generate_meta: bool = False,
    retry_of: Optional[int] = None,
) -> AuditOutput:
    """AI回答を意味監査する。

    question（質問）、response（AI回答）、reference（期待回答、省略可）を受け取り、
    PoR（S, C）、ΔE（意味ズレ量）、quality_score（品質スコア）、verdict（判定）を返す。
    結果はDBに自動保存される。

    question_meta が未提供の場合、auto_generate_meta=True なら LLM で動的生成を試みる。
    それでも生成できない場合は verdict="degraded" を返す。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
        session_id: セッションID（省略時は自動生成）
        question_meta: 問題メタデータ（core_propositions 等を含む dict）
        auto_generate_meta: question_meta 未提供時に LLM で動的生成する (opt-in)
    """
    # プロキシモード: UGH_REMOTE_API が設定されている場合、リモート API に転送
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_audit(
            remote_api, question=question, response=response,
            reference=reference, session_id=session_id,
            question_meta=question_meta,
            auto_generate_meta=auto_generate_meta,
            retry_of=retry_of,
        )

    db = _get_db()
    golden = _get_golden()

    ref = reference or golden.find_reference(question)
    errors: List[str] = []
    metadata_source = META_SOURCE_NONE

    # LLM meta 自動生成（opt-in）
    # 部分的な inline メタデータが提供された場合は欠損フィールドのみ補完する
    missing_fields = detect_missing_metadata(question_meta)
    if missing_fields and auto_generate_meta and _HAS_DETECTOR:
        try:
            from experiments.meta_generator import generate_meta
            generated = generate_meta(question)
            # 空リストは unfilled、空文字列は filled（trap_type="" は「罠なし」の明示指定）
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
    matched_id: Optional[str] = None

    # detect → calculate パイプライン
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
        question_id = "unknown"
        evidence = Evidence(question_id="unknown", f4_premise=None)
        if not question_meta:
            errors.append("question_meta_missing")

    state = calculate(evidence)

    # computed_components / missing_components
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

    gate = {
        "f1": evidence.f1_anchor,
        "f2": evidence.f2_unknown,
        "f3": evidence.f3_operator,
        "f4": evidence.f4_premise,
        "gate_verdict": gate_v,
        "primary_fail": _primary_fail_safe(
            evidence.f1_anchor, evidence.f2_unknown,
            evidence.f3_operator, evidence.f4_premise,
        ),
    }

    # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
    saved_id: Optional[int] = None
    if mode in ("computed", "computed_ai_draft"):
        saved_id = db.save(
            session_id=session_id or str(uuid.uuid4()),
            question=question,
            response=response,
            reference=ref,
            S=state.S,
            C=state.C,
            delta_e=state.delta_e,
            quality_score=state.quality_score,
            verdict=verdict,
            f1=evidence.f1_anchor,
            f2=evidence.f2_unknown,
            f3=evidence.f3_operator,
            f4=evidence.f4_premise if evidence.f4_premise is not None else 0.0,
            hit_rate=hit_rate or "",
            metadata_source=metadata_source,
            generated_meta=_json.dumps(
                question_meta or {}, ensure_ascii=False,
            ) if metadata_source == META_SOURCE_LLM else "",
            hit_sources=_json.dumps(
                evidence.hit_sources if hasattr(evidence, "hit_sources") else {},
                ensure_ascii=False,
            ),
            retry_of=retry_of,
        )

    degraded_reason = errors if mode == "degraded" else []

    # grv 計算 (SBert 依存、未導入時は None)
    grv_output: Optional[Dict] = None
    try:
        from grv_calculator import compute_grv
        grv_result = compute_grv(
            question=question,
            response_text=response,
            question_meta=question_meta,
            metadata_source=metadata_source,
            c_normalized=state.C,
        )
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
    except Exception:  # grv は補助計測器 — 失敗時は null フォールバック
        pass

    # response_mode_signal (deterministic, non-binding — fails silently)
    # Lookup priority: canonical reviewed > inline explicit > not_available
    # _ma_out is populated from the resolved source (same as signal scoring)
    _ms_output: Optional[Dict] = None
    _ma_out: Optional[Dict] = None
    try:
        from mode_signal import run_mode_signal
        _ms_output, _ma_out = run_mode_signal(
            response_text=response,
            question_id=question_id,
            question_meta=question_meta,
            evidence_primary=evidence.mode_affordance_primary,
            evidence_secondary=evidence.mode_affordance_secondary,
            evidence_closure=evidence.mode_affordance_closure,
            evidence_action_required=evidence.mode_affordance_action_required,
        )
    except Exception:
        _ms_output = None

    return AuditOutput(
        schema_version=SCHEMA_VERSION,
        S=state.S,
        C=None if metadata_source == META_SOURCE_FALLBACK else state.C,
        delta_e=None if metadata_source == META_SOURCE_FALLBACK else state.delta_e,
        quality_score=(
            None if metadata_source == META_SOURCE_FALLBACK else state.quality_score
        ),
        verdict=verdict,
        hit_rate=hit_rate,
        structural_gate=gate,
        saved_id=saved_id,
        mode=mode,
        is_reliable=is_reliable,
        matched_id=matched_id,
        metadata_source=metadata_source,
        computed_components=sorted(computed),
        missing_components=sorted(missing),
        errors=errors,
        degraded_reason=degraded_reason,
        mode_affordance=_ma_out,
        soft_rescue=rescue,
        grv=grv_output,
        response_mode_signal=_ms_output,
    )


def _proxy_get(remote_api: str, path: str) -> Dict:
    """リモート API から GET リクエストで取得"""
    import urllib.error
    import urllib.request
    url = remote_api.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"remote_api_error: {e.code}", "detail": body[:200]}
    except Exception as e:
        return {"error": f"remote_api_error: {e}"}


@mcp.tool()
def get_audit(audit_id: int) -> Dict:
    """ID指定で監査結果を1件取得する。

    Args:
        audit_id: 監査結果のID
    """
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/audit/{audit_id}")
    db = _get_db()
    row = db.get_by_id(audit_id)
    if row is None:
        return {"error": f"audit_id {audit_id} not found"}
    return row


@mcp.tool()
def get_history(limit: int = 20) -> List[Dict]:
    """直近の監査履歴を取得する。

    Args:
        limit: 取得件数 (デフォルト: 20)
    """
    limit = max(1, min(limit, 500))
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/history?limit={limit}")
    db = _get_db()
    return db.list_recent(limit=limit)


@mcp.tool()
def get_session_summary(session_id: str) -> Dict:
    """セッション単位の集計サマリーを取得する。

    Args:
        session_id: セッションID
    """
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        from urllib.parse import quote
        return _proxy_get(remote_api, f"/api/session/{quote(session_id, safe='')}")
    db = _get_db()
    return db.session_summary(session_id)


@mcp.tool()
def get_drift_history(limit: int = 100) -> List[Dict]:
    """ΔE時系列データを取得する。品質推移の分析に使用。

    Args:
        limit: 取得件数 (デフォルト: 100)
    """
    limit = max(1, min(limit, 1000))
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/drift?limit={limit}")
    db = _get_db()
    return db.drift_history(limit=limit)


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
    mcp.settings.streamable_http_path = "/mcp"

    mcp.run(transport="streamable-http")
