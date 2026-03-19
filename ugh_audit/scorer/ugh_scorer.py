"""
ugh_audit/scorer/ugh_scorer.py
UGH指標スコアラー: ugh3-metrics-libを呼び出してPoR/ΔE/grvを計算する
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import AuditResult

# ugh3-metrics-libが利用可能な場合はインポート
# 利用不可能な場合はフォールバック実装を使用
try:
    from ugh3_metrics.metrics import PorV4, DeltaE4, GrvV4
    from core.metrics import POR_FIRE_THRESHOLD
    _UGH3_AVAILABLE = True
except ImportError:
    _UGH3_AVAILABLE = False
    POR_FIRE_THRESHOLD = 0.82  # ugh3-metrics-libのデフォルト閾値


class UGHScorer:
    """
    UGH指標によるAI回答スコアラー

    ugh3-metrics-lib が利用可能な場合はそれを使用する。
    利用不可能な場合は sentence-transformers による
    フォールバック実装を使用する。

    Reference暫定基準（研究段階）:
        - PoR threshold: 0.82（ugh3-metrics-libデフォルト）
        - ΔE同一意味圏: <= 0.04（イラスト実験A群平均から流用）
        - ΔE意味乖離:   > 0.10（仕様書定義）
    """

    def __init__(self, model_id: str = "unknown"):
        self.model_id = model_id
        self._por = PorV4() if _UGH3_AVAILABLE else None
        self._delta_e = DeltaE4() if _UGH3_AVAILABLE else None
        self._grv = GrvV4() if _UGH3_AVAILABLE else None

        if not _UGH3_AVAILABLE:
            self._init_fallback()

    def _init_fallback(self) -> None:
        """ugh3-metrics-lib非依存のフォールバック実装"""
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            self._st_model = SentenceTransformer("all-MiniLM-L6-v2")
            self._np = np
            self._fallback_ready = True
        except ImportError:
            self._fallback_ready = False

    def score(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AuditResult:
        """
        AI回答をUGH指標でスコアリングする

        Args:
            question:   ユーザーの質問
            response:   AIの回答
            reference:  期待される回答（ΔE計算に使用）。
                        Noneの場合はquestionをreferenceとして使用
            session_id: セッション識別子

        Returns:
            AuditResult: PoR / ΔE / grv を含む監査結果
        """
        ref = reference or question
        sid = session_id or str(uuid.uuid4())[:8]

        if _UGH3_AVAILABLE:
            return self._score_with_ugh3(question, response, ref, sid)
        elif self._fallback_ready:
            return self._score_fallback(question, response, ref, sid)
        else:
            return self._score_minimal(question, response, ref, sid)

    def _score_with_ugh3(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
        """ugh3-metrics-libを使ったスコアリング"""
        por_result = self._por.compute(question, [response])
        delta_e_result = self._delta_e.compute(reference, response)
        grv_result = self._grv.compute(response)

        return AuditResult(
            question=question,
            response=response,
            reference=reference,
            por=por_result.score,
            por_fired=por_result.fired,
            delta_e=delta_e_result.score,
            grv=grv_result.weights,
            model_id=self.model_id,
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
        )

    def _score_fallback(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
        """sentence-transformersを使ったフォールバックスコアリング"""
        np = self._np
        model = self._st_model

        # PoR: 質問と回答のコサイン類似度
        q_emb = model.encode(question, normalize_embeddings=True)
        r_emb = model.encode(response, normalize_embeddings=True)
        ref_emb = model.encode(reference, normalize_embeddings=True)

        por = float(np.dot(q_emb, r_emb))
        por_fired = por >= POR_FIRE_THRESHOLD

        # ΔE: referenceと回答のコサイン距離
        delta_e = float(1.0 - np.dot(ref_emb, r_emb))
        delta_e = max(0.0, min(1.0, delta_e))

        # grv: 単純なTF近似（上位語彙の重みを返す）
        grv = self._compute_grv_simple(response)

        return AuditResult(
            question=question,
            response=response,
            reference=reference,
            por=por,
            por_fired=por_fired,
            delta_e=delta_e,
            grv=grv,
            model_id=self.model_id,
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
        )

    def _compute_grv_simple(self, text: str) -> dict:
        """簡易grv計算: 語彙頻度ベースの重力近似"""
        import re
        from collections import Counter

        # 日本語・英語の単語分割（簡易）
        words = re.findall(r'[一-龯ぁ-んァ-ン]+|[a-zA-Z]+', text)
        # ストップワード除外（簡易）
        stopwords = {'は', 'が', 'を', 'に', 'で', 'の', 'と', 'も', 'か',
                     'this', 'that', 'the', 'a', 'an', 'is', 'are', 'was'}
        words = [w for w in words if w not in stopwords and len(w) > 1]
        if not words:
            return {}

        counts = Counter(words)
        total = sum(counts.values())
        # 上位10語を正規化して返す
        return {w: round(c / total, 3) for w, c in counts.most_common(10)}

    def _score_minimal(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
        """依存ライブラリなしの最小実装（研究用途外・テスト向け）"""
        return AuditResult(
            question=question,
            response=response,
            reference=reference,
            por=0.0,
            por_fired=False,
            delta_e=0.0,
            grv={},
            model_id=self.model_id,
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
        )
