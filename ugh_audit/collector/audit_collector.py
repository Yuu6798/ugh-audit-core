"""
ugh_audit/collector/audit_collector.py
AuditCollector — AI回答ログの収集・監査パイプライン（パイプライン A 対応）

**DEPRECATED (v0.4, removal scheduled v0.5)**:
本モジュールは ``question_meta`` を受け取らない古いシグネチャで作られており、
現行パイプラインでは必然的に ``verdict="degraded"`` を返し実監査に到達できない。
programmatic API が必要な場合は REST/MCP 経由を利用すること。

- REST: ``POST /audit``  (``ugh_audit.server:app``)
- MCP:  ``audit_answer`` tool (``ugh_audit.mcp_server``)

マイグレーション詳細: docs/server_api.md
"""
from __future__ import annotations

import sys
import uuid
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from ..reference.golden_store import GoldenStore
from ..storage.audit_db import AuditDB

# パイプライン A のインポート
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
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


_DEPRECATION_MESSAGE = (
    "AuditCollector / SessionCollector are deprecated and will be removed in v0.5. "
    "They never receive question_meta and therefore always return verdict='degraded'. "
    "Use the REST endpoint POST /audit (ugh_audit.server) or the MCP tool "
    "audit_answer (ugh_audit.mcp_server) instead. See docs/server_api.md."
)


class AuditCollector:
    """
    Q&A ペアを受け取り、パイプライン A でスコアリング → DB保存 を一括で行うパイプライン。

    .. deprecated:: 0.4
        Use ``ugh_audit.server`` REST API or ``ugh_audit.mcp_server`` MCP tool
        instead. Scheduled for removal in v0.5.
    """

    def __init__(
        self,
        db: Optional[AuditDB] = None,
        golden: Optional[GoldenStore] = None,
    ):
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        self._db = db or AuditDB()
        self._golden = golden or GoldenStore()

    def collect(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        Q&A ペアを監査して DB に保存する。

        Returns:
            dict: 監査結果
        """
        ref = reference or self._golden.find_reference(question)

        evidence = Evidence(question_id="unknown", f4_premise=None)
        state = calculate(evidence)

        if state.C is not None and state.delta_e is not None:
            verdict = _verdict(state.delta_e)
        else:
            verdict = "degraded"

        hit_rate: Optional[str] = None
        if evidence.propositions_total > 0:
            hit_rate = f"{evidence.propositions_hit}/{evidence.propositions_total}"

        # degraded 時は DB に保存しない（未計算ログでベースラインを汚染させない）
        saved_id = None
        if verdict != "degraded":
            saved_id = self._db.save(
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

        return {
            "S": state.S,
            "C": state.C,
            "delta_e": state.delta_e,
            "quality_score": state.quality_score,
            "verdict": verdict,
            "hit_rate": hit_rate,
            "saved_id": saved_id,
        }

    def collect_batch(
        self,
        pairs: List[dict],
        session_id: Optional[str] = None,
    ) -> List[dict]:
        """
        複数の Q&A ペアを一括監査・保存。

        Args:
            pairs: [{"question": ..., "response": ..., "reference": ...}, ...]
        """
        results = []
        for pair in pairs:
            result = self.collect(
                question=pair["question"],
                response=pair["response"],
                reference=pair.get("reference"),
                session_id=session_id,
            )
            results.append(result)
        return results

    @contextmanager
    def session(self, session_id: str) -> Iterator["SessionCollector"]:
        """
        セッション単位でターンを記録するコンテキストマネージャ。

        with collector.session("session-001") as s:
            s.collect(question=..., response=...)
        summary = s.summary()
        """
        sc = SessionCollector(session_id=session_id, collector=self)
        yield sc


class SessionCollector:
    """session() コンテキスト内で使うセッション単位のコレクター

    .. deprecated:: 0.4
        See :class:`AuditCollector` for migration path.
    """

    def __init__(self, session_id: str, collector: AuditCollector):
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        self.session_id = session_id
        self._collector = collector
        self._results: List[dict] = []

    def collect(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
    ) -> dict:
        result = self._collector.collect(
            question=question,
            response=response,
            reference=reference,
            session_id=self.session_id,
        )
        self._results.append(result)
        return result

    def summary(self) -> dict:
        """このセッションの集計サマリーを返す"""
        return self._collector._db.session_summary(self.session_id)

    @property
    def results(self) -> List[dict]:
        return list(self._results)
