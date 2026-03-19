"""
ugh_audit/scorer/ugh_scorer.py
UGH指標スコアラー

フォールバック構成（Step 0修正済み）:
    Layer 1: ugh3-metrics-lib（本命）— 防御的importで安全に接続
    Layer 2: sentence-transformers + fugashi（強化フォールバック）
    Layer 3: minimal stub（テスト用）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import AuditResult

# ------------------------------------------------------------------ #
# Step 0: ugh3-metrics-lib 防御的 import
# - パッケージ構造が変わっても落ちないよう多段 try で吸収
# - POR_FIRE_THRESHOLD は ugh3 側になければデフォルト値を使う
# ------------------------------------------------------------------ #
_UGH3_AVAILABLE = False
POR_FIRE_THRESHOLD = 0.82  # ugh3-metrics-lib デフォルト値

try:
    from ugh3_metrics.metrics import PorV4, DeltaE4, GrvV4  # type: ignore[import]
    _UGH3_AVAILABLE = True
    try:
        from ugh3_metrics.core.metrics import POR_FIRE_THRESHOLD  # type: ignore[import]
    except ImportError:
        try:
            from core.metrics import POR_FIRE_THRESHOLD  # type: ignore[import]
        except ImportError:
            pass  # デフォルト値 0.82 を使い続ける
except ImportError:
    pass

# ------------------------------------------------------------------ #
# Step 1: sentence-transformers 可用性チェック（起動時に1回だけ）
# ------------------------------------------------------------------ #
_ST_AVAILABLE = False
_ST_MODEL = None
_NP = None

try:
    import numpy as _np_module
    from sentence_transformers import SentenceTransformer as _ST  # type: ignore[import]
    _ST_MODEL = _ST("paraphrase-multilingual-MiniLM-L12-v2")  # 多言語モデル
    _NP = _np_module
    _ST_AVAILABLE = True
except Exception:
    pass

# ------------------------------------------------------------------ #
# Step 2: fugashi トークナイザー可用性チェック
# ------------------------------------------------------------------ #
_FUGASHI_AVAILABLE = False
_TAGGER = None

try:
    import fugashi  # type: ignore[import]
    _TAGGER = fugashi.Tagger()
    _FUGASHI_AVAILABLE = True
except Exception:
    pass


class UGHScorer:
    """
    UGH指標（PoR / ΔE / grv）によるAI回答スコアラー

    Reference暫定基準（研究段階・Claw判断）:
        PoR threshold : 0.82（ugh3-metrics-lib デフォルト）
        ΔE 同一意味圏: <= 0.04（イラスト実験 A群平均から流用）
        ΔE 意味乖離  : > 0.10（SVP仕様書定義）
    """

    def __init__(self, model_id: str = "unknown"):
        self.model_id = model_id

        # ugh3 インスタンス生成（引数なしで安全に初期化）
        if _UGH3_AVAILABLE:
            try:
                self._por = PorV4()
                self._delta_e = DeltaE4()
                self._grv = GrvV4()
            except Exception:
                # インスタンス化に失敗したら ugh3 を無効化
                self._por = self._delta_e = self._grv = None
                globals()["_UGH3_AVAILABLE"] = False  # type: ignore

    # -------------------------------------------------------------- #
    # Public API
    # -------------------------------------------------------------- #

    def score(
        self,
        question: str,
        response: str,
        reference: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AuditResult:
        """
        AI回答を UGH 指標でスコアリングする

        Args:
            question  : ユーザーの質問
            response  : AI の回答
            reference : 期待される回答（ΔE 計算用）。
                        None の場合は question を代用
            session_id: セッション識別子
        """
        ref = reference or question
        sid = session_id or str(uuid.uuid4())[:8]

        if _UGH3_AVAILABLE and self._por is not None:
            return self._score_with_ugh3(question, response, ref, sid)
        elif _ST_AVAILABLE:
            return self._score_with_st(question, response, ref, sid)
        else:
            return self._score_minimal(question, response, ref, sid)

    @property
    def backend(self) -> str:
        """使用中のスコアリングバックエンド名を返す"""
        if _UGH3_AVAILABLE:
            return "ugh3-metrics-lib"
        elif _ST_AVAILABLE:
            return "sentence-transformers"
        return "minimal"

    # -------------------------------------------------------------- #
    # Layer 1: ugh3-metrics-lib
    # -------------------------------------------------------------- #

    def _score_with_ugh3(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
        try:
            por_result = self._por.compute(question, [response])
            delta_e_result = self._delta_e.compute(reference, response)
            grv_result = self._grv.compute(response)
            return AuditResult(
                question=question,
                response=response,
                reference=reference,
                por=float(por_result.score),
                por_fired=bool(por_result.fired),
                delta_e=float(delta_e_result.score),
                grv=dict(grv_result.weights),
                model_id=self.model_id,
                session_id=session_id,
                created_at=datetime.now(timezone.utc),
            )
        except Exception:
            # ugh3 が実行時エラーの場合も ST にフォールバック
            if _ST_AVAILABLE:
                return self._score_with_st(question, response, reference, session_id)
            return self._score_minimal(question, response, reference, session_id)

    # -------------------------------------------------------------- #
    # Layer 2: sentence-transformers + fugashi
    # -------------------------------------------------------------- #

    def _score_with_st(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
        np = _NP
        model = _ST_MODEL

        q_emb = model.encode(question, normalize_embeddings=True)
        r_emb = model.encode(response, normalize_embeddings=True)
        ref_emb = model.encode(reference, normalize_embeddings=True)

        # PoR: 質問と回答のコサイン類似度
        por = float(np.dot(q_emb, r_emb))
        por = max(0.0, min(1.0, por))
        por_fired = por >= POR_FIRE_THRESHOLD

        # ΔE: reference と回答のコサイン距離
        delta_e = float(1.0 - np.dot(ref_emb, r_emb))
        delta_e = max(0.0, min(1.0, delta_e))

        # grv: fugashi or 正規表現フォールバック
        grv = self._compute_grv(response)

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

    # -------------------------------------------------------------- #
    # grv 計算（Step 2: fugashi 対応）
    # -------------------------------------------------------------- #

    def _compute_grv(self, text: str) -> dict:
        """
        grv 計算

        fugashi が使える場合: 形態素解析 → 名詞・動詞・形容詞の原形を抽出
        使えない場合: 正規表現で漢字/カタカナ/英語ブロックを抽出（従来）

        TF-IDF 近似として、頻度 / 文書長 で正規化し上位 10 語を返す。
        """
        if _FUGASHI_AVAILABLE:
            return self._grv_with_fugashi(text)
        return self._grv_with_regex(text)

    def _grv_with_fugashi(self, text: str) -> dict:
        from collections import Counter

        # 対象品詞: 名詞・動詞（基本形）・形容詞（基本形）
        TARGET_POS = {"名詞", "動詞", "形容詞"}
        STOPWORDS = {
            "する", "ある", "いる", "なる", "れる", "られる",
            "こと", "もの", "ため", "よう", "それ", "これ",
        }

        words = []
        for word in _TAGGER(text):
            pos = word.feature.pos1 if hasattr(word.feature, "pos1") else str(word.feature).split(",")[0]
            if pos not in TARGET_POS:
                continue
            # 原形を使う（なければ表層形）
            surface = word.feature.lemma if hasattr(word.feature, "lemma") and word.feature.lemma else word.surface
            if surface and surface not in STOPWORDS and len(surface) > 1:
                words.append(surface)

        if not words:
            return self._grv_with_regex(text)  # fallback

        counts = Counter(words)
        total = sum(counts.values())
        return {w: round(c / total, 3) for w, c in counts.most_common(10)}

    def _grv_with_regex(self, text: str) -> dict:
        import re
        from collections import Counter

        # 漢字ブロック・ひらがな連続・カタカナ連続・英単語を抽出
        words = re.findall(r'[一-龯]{2,}|[ぁ-ん]{3,}|[ァ-ヴ]{2,}|[a-zA-Z]{3,}', text)
        STOPWORDS = {
            'は', 'が', 'を', 'に', 'で', 'の', 'と', 'も', 'か',
            'this', 'that', 'the', 'and', 'for',
        }
        words = [w for w in words if w not in STOPWORDS]
        if not words:
            return {}

        counts = Counter(words)
        total = sum(counts.values())
        return {w: round(c / total, 3) for w, c in counts.most_common(10)}

    # -------------------------------------------------------------- #
    # Layer 3: minimal stub
    # -------------------------------------------------------------- #

    def _score_minimal(
        self, question: str, response: str, reference: str, session_id: str
    ) -> AuditResult:
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
