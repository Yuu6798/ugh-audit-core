"""
ugh_audit/collector/audit_collector.py
AuditCollector — AI回答ログの収集・自動スコアリングパイプライン

使い方:
    collector = AuditCollector(model_id="claw-v1")

    # 1回の Q&A をスコアリングして自動保存
    result = collector.collect(
        question="AIは意味を持てるか？",
        response="AIは意味と共振する動的プロセスです。",
    )

    # セッション単位で複数ターンを記録
    with collector.session("session-abc") as s:
        s.collect(question=..., response=...)
        s.collect(question=..., response=...)
    summary = s.summary()
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List, Optional

from ..reference.golden_store import GoldenStore
from ..scorer.models import AuditResult
from ..scorer.ugh_scorer import UGHScorer
from ..storage.audit_db import AuditDB


class AuditCollector:
    """
    Q&A ペアを受け取り、スコアリング → DB保存 を一括で行うパイプライン。

    referenceは以下の優先順位で決定:
        1. collect() に明示的に渡された reference
        2. GoldenStore から question に近いエントリを自動検索
        3. question をそのまま代用（フォールバック）
    """

    def __init__(
        self,
        model_id: str = "unknown",
        db: Optional[AuditDB] = None,
        golden: Optional[GoldenStore] = None,
    ):
        self.model_id = model_id
        self._scorer = UGHScorer(model_id=model_id)
        self._db = db or AuditDB()
        self._golden = golden or GoldenStore()

    @property
    def backend(self) -> str:
        """使用中のスコアリングバックエンド"""
        return self._scorer.backend

    def collect(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
        session_id: Optional[str] = None,
        *,
        reference_core: Optional[str] = None,
    ) -> AuditResult:
        """
        Q&A ペアをスコアリングして DB に保存する。

        Returns:
            AuditResult: スコアリング結果（保存済み）
        """
        # reference の自動解決
        ref = reference or self._golden.find_reference(question)

        result = self._scorer.score(
            question=question,
            response=response,
            reference=ref,
            reference_core=reference_core,
            session_id=session_id,
        )
        self._db.save(result)
        return result

    def collect_batch(
        self,
        pairs: List[dict],
        session_id: Optional[str] = None,
    ) -> List[AuditResult]:
        """
        複数の Q&A ペアを一括スコアリング・保存。

        Args:
            pairs: [{"question": ..., "response": ..., "reference": ...}, ...]
        """
        results = []
        for pair in pairs:
            result = self.collect(
                question=pair["question"],
                response=pair["response"],
                reference=pair.get("reference"),
                reference_core=pair.get("reference_core"),
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
    """session() コンテキスト内で使うセッション単位のコレクター"""

    def __init__(self, session_id: str, collector: AuditCollector):
        self.session_id = session_id
        self._collector = collector
        self._results: List[AuditResult] = []

    def collect(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
        *,
        reference_core: Optional[str] = None,
    ) -> AuditResult:
        result = self._collector.collect(
            question=question,
            response=response,
            reference=reference,
            reference_core=reference_core,
            session_id=self.session_id,
        )
        self._results.append(result)
        return result

    def summary(self) -> dict:
        """このセッションの集計サマリーを返す"""
        return self._collector._db.session_summary(self.session_id)

    @property
    def results(self) -> List[AuditResult]:
        return list(self._results)
