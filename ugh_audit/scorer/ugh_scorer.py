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
        """
        ugh3-metrics-lib の実API に合わせた native 経路。

        ugh3 API 契約（ugh3-metrics-lib 実装より）:
            PorV4().score(a: str, b: str) -> float  ※シグモイド変換済み 0-1
            DeltaE4().score(a: str, b: str) -> float  ※コサイン距離 0-1
            GrvV4().score(a: str, b: str) -> float  ※TF-IDF+PMI+Entropy 0-1
            POR_FIRE_THRESHOLD = 0.82 (core/metrics.py)

        .fired / .weights プロパティは存在しない。
        全て float を直接返すので自前で判定・変換する。
        """
        try:
            por: float = float(self._por.score(question, response))
            por_fired: bool = por >= POR_FIRE_THRESHOLD

            delta_e: float = float(self._delta_e.score(reference, response))
            delta_e = max(0.0, min(1.0, delta_e))

            # GrvV4.score(a, b) は b を無視して a のスカラー重力値を返す
            # 辞書形式の grv はフォールバック実装で補完する
            grv_scalar: float = float(self._grv.score(response, ""))
            grv: dict = {"_grv_scalar": round(grv_scalar, 4)}
            # 語彙分布は ST フォールバックの grv 計算で補完
            grv.update(self._compute_grv(response))

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
        import re
        from collections import Counter

        # 対象品詞: 名詞のみ（動詞・形容詞は機能語混入が多いため除外）
        TARGET_POS = {"名詞"}

        # ストップワード: 機能語・形式名詞・高頻度非内容語・GPT定型句
        STOPWORDS = {
            # 助詞・助動詞
            "は", "が", "の", "を", "に", "へ", "と", "で", "も", "か",
            "です", "ます", "する", "した", "している", "される", "された",
            "ある", "ない", "いる", "なる", "できる", "れる", "られる",
            # 接続・指示
            "この", "その", "あの", "これ", "それ", "あれ",
            "また", "しかし", "ただし", "そして", "さらに", "つまり",
            # 形式名詞
            "ため", "こと", "もの", "ところ", "よう", "ほう",
            "場合", "必要", "可能", "重要", "特に", "非常",
            # 高頻度非内容語（GPT回答で過剰出現）
            "回答", "質問", "説明", "以下", "例えば", "について",
            # 機能語的末尾（fugashi が名詞として取得する場合がある）
            "があります", "ています", "ません", "でしょう", "かもしれません",
            "いことは", "します", "である", "として",
            # 不完全カタカナ断片（2文字以下の断片）
            "スパ", "チュ", "フレ", "ムワ", "パタ", "ション",
        }

        words = []
        buffer_kata = ""

        for word in _TAGGER(text):
            pos = word.feature.pos1 if hasattr(word.feature, "pos1") else str(word.feature).split(",")[0]
            surface = word.surface

            # カタカナ隣接トークンを結合（長音・複合語の分断対策）
            if re.match(r'^[\u30A0-\u30FF\u30FC]+$', surface):
                buffer_kata += surface
                continue
            else:
                if buffer_kata:
                    if len(buffer_kata) >= 2 and buffer_kata not in STOPWORDS:
                        words.append(buffer_kata)
                    buffer_kata = ""

            if pos not in TARGET_POS:
                continue

            # 原形を使う（なければ表層形）
            lemma = word.feature.lemma if hasattr(word.feature, "lemma") and word.feature.lemma else surface

            # フィルタ: ストップワード除外、2文字以上
            if lemma and lemma not in STOPWORDS and len(lemma) >= 2:
                words.append(lemma)

        # バッファ残りを処理
        if buffer_kata and len(buffer_kata) >= 2 and buffer_kata not in STOPWORDS:
            words.append(buffer_kata)

        if not words:
            return self._grv_with_regex(text)  # fallback

        counts = Counter(words)
        total = sum(counts.values())
        return {w: round(c / total, 3) for w, c in counts.most_common(10)}

    def _grv_with_regex(self, text: str) -> dict:
        import re
        from collections import Counter

        # 漢字ブロック・カタカナ連続（3文字以上）・英単語を抽出
        words = re.findall(r'[一-龯]{2,}|[ァ-ヴ\u30FC]{3,}|[a-zA-Z]{3,}', text)
        STOPWORDS = {
            # 日本語機能語
            "は", "が", "を", "に", "で", "の", "と", "も", "か",
            "場合", "必要", "可能", "重要", "回答", "説明", "以下",
            "こと", "もの", "ため", "よう", "ほう", "として",
            # 英語機能語
            "this", "that", "the", "and", "for", "are", "not", "with",
        }
        words = [w for w in words if w not in STOPWORDS and len(w) >= 2]
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
