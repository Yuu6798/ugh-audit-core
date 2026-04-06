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

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .reference.golden_store import GoldenStore
from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import Evidence, calculate  # noqa: E402

# detector（検出層）— 利用可能な場合のみ使用
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


def _gate_verdict(f1: float, f2: float, f3: float, f4: float) -> str:
    vals = [f1, f2, f3, f4]
    fail_max = max(vals)
    if fail_max == 0.0:
        return "pass"
    if fail_max >= 1.0:
        return "fail"
    return "warn"


def _gate_verdict_safe(f1: float, f2: float, f3: float, f4: Optional[float]) -> str:
    vals = [f1, f2, f3] + ([f4] if f4 is not None else [])
    fail_max = max(vals) if vals else 0.0
    if fail_max == 0.0:
        return "pass"
    if fail_max >= 1.0:
        return "fail"
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
    matched_id: Optional[str]
    metadata_source: str
    computed_components: List[str] = field(default_factory=list)
    missing_components: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    degraded_reason: List[str] = field(default_factory=list)


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
) -> AuditOutput:
    """AI回答を意味監査する。

    question（質問）、response（AI回答）、reference（期待回答、省略可）を受け取り、
    PoR（S, C）、ΔE（意味ズレ量）、quality_score（品質スコア）、verdict（判定）を返す。
    結果はDBに自動保存される。

    question_meta が未提供の場合、命題カバレッジ（C）が計算できないため
    verdict="degraded" を返す。verdict="accept" は本計算完了時のみ返される。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
        session_id: セッションID（省略時は自動生成）
        question_meta: 問題メタデータ（core_propositions 等を含む dict）
    """
    db = _get_db()
    golden = _get_golden()

    ref = reference or golden.find_reference(question)
    errors: List[str] = []
    metadata_source = "none"
    matched_id: Optional[str] = None

    # detect → calculate パイプライン
    detected = False
    if question_meta and _HAS_DETECTOR:
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

    gate = {
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
    }

    # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
    saved_id: Optional[int] = None
    if mode == "computed":
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
        )

    degraded_reason = errors if mode != "computed" else []

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
        matched_id=matched_id,
        metadata_source=metadata_source,
        computed_components=sorted(computed),
        missing_components=sorted(missing),
        errors=errors,
        degraded_reason=degraded_reason,
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
