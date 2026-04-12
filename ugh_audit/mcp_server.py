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
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .metadata_generator import detect_missing_metadata
from .reference.golden_store import GoldenStore
from .soft_rescue import maybe_build_soft_rescue
from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import (  # noqa: E402
    Evidence,
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

# --- 定数 ---
SCHEMA_VERSION = "2.0.0"
GATE_FAIL = "fail"


def _gate_verdict(f1: float, f2: float, f3: float, f4: float) -> str:
    vals = [f1, f2, f3, f4]
    fail_max = max(vals)
    if fail_max == 0.0:
        return "pass"
    if fail_max >= 1.0:
        return GATE_FAIL
    return "warn"


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
    soft_rescue: Optional[Dict] = None


# ---------------------------------------------------------------------------
# ツール定義
# ---------------------------------------------------------------------------


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
    db = _get_db()
    golden = _get_golden()

    ref = reference or golden.find_reference(question)
    errors: List[str] = []
    metadata_source = "none"

    # LLM meta 自動生成（opt-in）
    missing_fields = detect_missing_metadata(question_meta)
    if missing_fields and auto_generate_meta and _HAS_DETECTOR:
        try:
            from experiments.meta_generator import generate_meta
            question_meta = generate_meta(question)
            metadata_source = "llm_generated"
        except Exception:
            pass  # silent fallback to degraded
    matched_id: Optional[str] = None

    # detect → calculate パイプライン
    detected = False
    if question_meta and _HAS_DETECTOR:
        if metadata_source != "llm_generated":
            metadata_source = "inline"
        question_id = question_meta.get("id", "unknown")
        matched_id = question_id
        if "question" not in question_meta:
            question_meta = {**question_meta, "question": question}
        evidence = _detect(question_id, response, question_meta)
        detected = True
    else:
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

    # fail-closed: verdict/mode が想定値であることを保証
    assert verdict in VALID_VERDICTS, f"invalid verdict: {verdict}"
    assert mode in VALID_MODES, f"invalid mode: {mode}"

    hit_rate: Optional[str] = None
    if evidence.propositions_total > 0:
        hit_rate = f"{evidence.propositions_hit}/{evidence.propositions_total}"

    # soft rescue (AI 草案メタデータで C=0 のとき部分ヒットを回収)
    metadata_confidence = (question_meta or {}).get("metadata_confidence")
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
            ) if metadata_source == "llm_generated" else "",
            hit_sources=_json.dumps(
                evidence.hit_sources if hasattr(evidence, "hit_sources") else {},
                ensure_ascii=False,
            ),
            retry_of=retry_of,
        )

    degraded_reason = errors if mode == "degraded" else []

    return AuditOutput(
        schema_version=SCHEMA_VERSION,
        S=state.S,
        C=state.C,
        delta_e=state.delta_e,
        quality_score=state.quality_score,
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
        soft_rescue=rescue,
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
    mcp.settings.streamable_http_path = "/mcp"

    mcp.run(transport="streamable-http")
