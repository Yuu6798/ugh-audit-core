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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from mcp.server.fastmcp import FastMCP

from .reference.golden_store import GoldenStore
from .storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import Evidence, calculate  # noqa: E402

# --- verdict ロジック（暫定閾値） ---
_VERDICT_ACCEPT = 0.10
_VERDICT_REWRITE = 0.25


def _verdict(delta_e: float) -> str:
    if delta_e <= _VERDICT_ACCEPT:
        return "accept"
    if delta_e <= _VERDICT_REWRITE:
        return "rewrite"
    return "regenerate"


def _gate_verdict(f1: float, f2: float, f3: float, f4: float) -> str:
    fail_max = max(f1, f2, f3, f4)
    if fail_max == 0.0:
        return "pass"
    if fail_max >= 1.0:
        return "fail"
    return "warn"


def _primary_fail(f1: float, f2: float, f3: float, f4: float) -> str:
    worst = max(f1, f2, f3, f4)
    if worst == 0.0:
        return "none"
    labels = {"f1": f1, "f2": f2, "f3": f3, "f4": f4}
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

    S: float
    C: float
    delta_e: float
    quality_score: float
    verdict: str
    hit_rate: str
    structural_gate: Dict
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
    S（構造完全性）、C（命題被覆率）、ΔE（意味距離）、quality_score（品質スコア）、
    verdict（判定）を返す。結果はDBに自動保存される。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
        session_id: セッションID（省略時は空文字列）
    """
    db = _get_db()
    golden = _get_golden()

    ref = reference or golden.find_reference(question)

    evidence = Evidence(question_id="unknown")
    state = calculate(evidence)
    verdict = _verdict(state.delta_e)

    hit_rate = ""
    if evidence.propositions_total > 0:
        hit_rate = f"{evidence.propositions_hit}/{evidence.propositions_total}"

    gate = {
        "f1": evidence.f1_anchor,
        "f2": evidence.f2_unknown,
        "f3": evidence.f3_operator,
        "f4": evidence.f4_premise,
        "gate_verdict": _gate_verdict(
            evidence.f1_anchor, evidence.f2_unknown,
            evidence.f3_operator, evidence.f4_premise,
        ),
        "primary_fail": _primary_fail(
            evidence.f1_anchor, evidence.f2_unknown,
            evidence.f3_operator, evidence.f4_premise,
        ),
    }

    saved_id = db.save(
        session_id=session_id or "",
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
        f4=evidence.f4_premise,
        hit_rate=hit_rate,
    )

    return AuditOutput(
        S=state.S,
        C=state.C,
        delta_e=state.delta_e,
        quality_score=state.quality_score,
        verdict=verdict,
        hit_rate=hit_rate,
        structural_gate=gate,
        saved_id=saved_id,
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
