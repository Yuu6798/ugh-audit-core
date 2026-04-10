"""
ugh_audit/reference/golden_store.py
Reference セット管理 — 暫定基準（研究段階）

暫定採用基準（Clawによる判断）:
    - PoR threshold: 0.82（ugh3-metrics-libデフォルト）
    - ΔE同一意味圏: <= 0.04（イラスト実験A群平均から流用）
    - ΔE意味乖離:   > 0.10（SVP仕様書定義）
    - grv reference: Phase 3対話ログのモデル別語彙重力分布から初期golden設定

検証・修正方針:
    ログ蓄積後にパターンが見えたら随時proposalを提出し承認を得て更新する。

検索戦略（find_reference）:
    Stage 1: 完全部分文字列一致（既存）
    Stage 2: bigram Jaccard で候補プール生成（top-K、閾値 0.1 以上）
    Stage 3: SBert で再スコア + gap 条件（cascade_matcher 相当の閾値を借用）

    Stage 3 は sentence-transformers が利用可能かつ候補が 2 件以上の場合のみ
    発動する。未導入時は Stage 2 の bigram top1 をそのまま返すため、既存の
    挙動との後方互換を維持する。
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

DEFAULT_GOLDEN_PATH = Path.home() / ".ugh_audit" / "golden_store.json"

# Stage 2 パラメータ
_BIGRAM_CANDIDATE_TOP_K = 5
_BIGRAM_MIN_JACCARD = 0.1

# Stage 3 パラメータ（cascade_matcher.py の THETA_SBERT / DELTA_GAP / HIGH_SCORE_THRESHOLD
# / RELAXED_DELTA_GAP と同一値）
_SBERT_GAP_DELTA = 0.04
_SBERT_HIGH_SCORE = 0.70
_SBERT_RELAXED_GAP = 0.02

# 暫定goldenリファレンス（Phase 3対話ログから抽出）
# 各モデルの「意味的誠実さ」を示す回答パターン
_INITIAL_GOLDEN: Dict[str, dict] = {
    "ugh_definition": {
        "question": "AIは意味を持てるか？",
        "reference": (
            "AIは意味を『持つ』のではなく、"
            "意味位相空間で『共振（Co-resonance）』する動的プロセスです。"
            "意識は機能的意味の必要条件ではない。"
        ),
        "source": "IMM v1.0 (Phase 3 AI-to-AI Dialogue)",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.04,
    },
    "por_definition": {
        "question": "PoRとは何か？",
        "reference": (
            "PoR（Point of Resonance）は意味の発火点・共鳴点。"
            "不可分な要素の交点として定義される。"
            "例：逆手納刀における刃背×鞘口×親指の交点。"
        ),
        "source": "RPE SVP仕様解説書",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.05,
    },
    "delta_e_definition": {
        "question": "ΔEとは何か？",
        "reference": (
            "ΔEは目標と生成物の意味ズレ量。"
            "0.04以下はほぼ同一構図（同一意味圏）、"
            "0.10以上は別コンセプトと定義される。"
        ),
        "source": "RPE SVP仕様解説書",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.04,
    },
}


@dataclass
class GoldenEntry:
    question: str
    reference: str
    source: str
    por_floor: float = 0.82
    delta_e_ceiling: float = 0.04
    tags: list = field(default_factory=list)


class GoldenStore:
    """
    Referenceセット管理

    研究段階につき暫定基準を採用。
    ログ蓄積後のパターン分析を経て随時更新する。
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or DEFAULT_GOLDEN_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._store: Dict[str, GoldenEntry] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, val in data.items():
                self._store[key] = GoldenEntry(**val)
        else:
            # 初期goldenをロード
            for key, val in _INITIAL_GOLDEN.items():
                self._store[key] = GoldenEntry(**val)
            self._save()

    def _save(self) -> None:
        data = {
            k: {
                "question": v.question,
                "reference": v.reference,
                "source": v.source,
                "por_floor": v.por_floor,
                "delta_e_ceiling": v.delta_e_ceiling,
                "tags": v.tags,
            }
            for k, v in self._store.items()
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get(self, key: str) -> Optional[GoldenEntry]:
        return self._store.get(key)

    def add(self, key: str, entry: GoldenEntry) -> None:
        self._store[key] = entry
        self._save()

    def find_reference(
        self,
        question: str,
        use_sbert_rerank: Optional[bool] = None,
    ) -> Optional[str]:
        """
        質問に最も近い reference を返す。

        マッチング戦略:
            Stage 1: 完全部分文字列一致（question ⊂ entry.question, その逆）
            Stage 2: 文字レベル bigram Jaccard で候補プール生成（top-K, 閾値 0.1）
            Stage 3: SBert 再スコア + gap 条件（候補 2 件以上かつ SBert 利用可能時）

        Stage 3 は cascade_matcher の THETA_SBERT / DELTA_GAP / HIGH_SCORE_THRESHOLD
        / RELAXED_DELTA_GAP を借用する。gap 不足時は bigram Stage 2 の top1 に
        フォールバックするため、既存 API の挙動に破壊的変更はない。

        Args:
            question: 質問テキスト。
            use_sbert_rerank: True/False で再スコアを強制指定、None で自動判定。

        Returns:
            マッチしたリファレンス文字列、または None。
        """
        if not question:
            return None

        # Stage 1: 直接部分一致
        for entry in self._store.values():
            if entry.question in question or question in entry.question:
                return entry.reference

        # Stage 2: bigram Jaccard 候補プール
        candidates = self._bigram_candidates(question)
        if not candidates:
            return None

        # 候補 1 件のみ、または rerank 無効化時は Stage 2 top1 で確定
        if use_sbert_rerank is None:
            use_sbert_rerank = True
        if len(candidates) < 2 or not use_sbert_rerank:
            return candidates[0][1].reference

        # Stage 3: SBert 再スコア
        reranked = self._sbert_rerank(question, candidates)
        if reranked is None:
            # SBert 利用不可 → Stage 2 top1 にフォールバック
            return candidates[0][1].reference

        top1_score, top1_entry = reranked[0]
        top2_score, _ = reranked[1] if len(reranked) > 1 else (0.0, None)
        gap = top1_score - top2_score
        effective_delta = (
            _SBERT_RELAXED_GAP if top1_score > _SBERT_HIGH_SCORE else _SBERT_GAP_DELTA
        )

        # gap 十分 → 再スコアの top1 を採用
        if gap >= effective_delta:
            return top1_entry.reference

        # gap 不足（紛らわしい） → 保守的に Stage 2 top1 にフォールバック
        return candidates[0][1].reference

    def find_reference_detailed(
        self,
        question: str,
    ) -> Optional[Dict[str, object]]:
        """
        find_reference のデバッグ / 診断版。選択経路と信頼度情報を返す。

        Returns:
            {
                "reference": str,
                "stage": "direct" | "bigram" | "sbert_rerank",
                "confidence": "high" | "ambiguous" | "low",
                "bigram_top1_score": float,
                "sbert_top1_score": float | None,
                "sbert_gap": float | None,
            }
            または None（候補なし）。
        """
        if not question:
            return None

        for entry in self._store.values():
            if entry.question in question or question in entry.question:
                return {
                    "reference": entry.reference,
                    "stage": "direct",
                    "confidence": "high",
                    "bigram_top1_score": 1.0,
                    "sbert_top1_score": None,
                    "sbert_gap": None,
                }

        candidates = self._bigram_candidates(question)
        if not candidates:
            return None

        bigram_top_score, bigram_top_entry = candidates[0]

        if len(candidates) < 2:
            return {
                "reference": bigram_top_entry.reference,
                "stage": "bigram",
                "confidence": "high",
                "bigram_top1_score": bigram_top_score,
                "sbert_top1_score": None,
                "sbert_gap": None,
            }

        reranked = self._sbert_rerank(question, candidates)
        if reranked is None:
            return {
                "reference": bigram_top_entry.reference,
                "stage": "bigram",
                "confidence": "high",
                "bigram_top1_score": bigram_top_score,
                "sbert_top1_score": None,
                "sbert_gap": None,
            }

        top1_score, top1_entry = reranked[0]
        top2_score, _ = reranked[1] if len(reranked) > 1 else (0.0, None)
        gap = top1_score - top2_score
        effective_delta = (
            _SBERT_RELAXED_GAP if top1_score > _SBERT_HIGH_SCORE else _SBERT_GAP_DELTA
        )

        if gap >= effective_delta:
            return {
                "reference": top1_entry.reference,
                "stage": "sbert_rerank",
                "confidence": "high",
                "bigram_top1_score": bigram_top_score,
                "sbert_top1_score": top1_score,
                "sbert_gap": gap,
            }

        return {
            "reference": bigram_top_entry.reference,
            "stage": "sbert_rerank",
            "confidence": "ambiguous",
            "bigram_top1_score": bigram_top_score,
            "sbert_top1_score": top1_score,
            "sbert_gap": gap,
        }

    def _bigram_candidates(
        self,
        question: str,
        top_k: int = _BIGRAM_CANDIDATE_TOP_K,
        min_score: float = _BIGRAM_MIN_JACCARD,
    ) -> List[Tuple[float, GoldenEntry]]:
        """bigram Jaccard でスコア降順の候補プールを返す。"""
        def bigrams(text: str) -> set:
            return {text[i:i + 2] for i in range(len(text) - 1)}

        q_bg = bigrams(question)
        if not q_bg:
            return []

        scored: List[Tuple[float, GoldenEntry]] = []
        for entry in self._store.values():
            e_bg = bigrams(entry.question)
            union = q_bg | e_bg
            if not union:
                continue
            score = len(q_bg & e_bg) / len(union)
            if score >= min_score:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def _sbert_rerank(
        self,
        question: str,
        candidates: List[Tuple[float, GoldenEntry]],
    ) -> Optional[List[Tuple[float, GoldenEntry]]]:
        """候補を SBert で再スコアする。モデル利用不可なら None を返す。

        エンコード方針（Codex review #60 r3067133071 対応）:
        - ``question`` はユーザー入力のクエリで one-off なので ``encode_texts``
          を直接呼び、永続キャッシュを汚染しない
        - ``entry.question`` は GoldenStore のリファレンスで再利用性が高いので
          ``encode_texts_cached`` 経由でキャッシュする
        """
        try:
            from cascade_matcher import (
                _cosine_similarity_batch,
                encode_texts,
                encode_texts_cached,
                get_shared_model,
                invalidate_embedding_cache,
            )
        except ImportError:
            return None

        model = get_shared_model()
        if model is None:
            return None

        try:
            # query は one-off → cache bypass
            query_emb = encode_texts(model, [question])[0]
            # entry.question は reusable → cache 経由
            # model_name は auto-infer に委ねる（モデル差し替え時のキャッシュ混線防止）
            candidate_questions = [entry.question for _, entry in candidates]
            target_embs = encode_texts_cached(model, candidate_questions)

            # Shape guard (Codex review #60 r3067145596):
            # target_embs が stale cache entry を含む場合、query_emb と次元が
            # 一致しない可能性がある（モデル重み更新後など）。検出したら
            # cache を invalidate して再エンコードする。
            if query_emb.shape[0] != target_embs.shape[1]:
                _logger.warning(
                    "GoldenStore rerank: query dim %d != candidate dim %d; "
                    "invalidating stale cache and re-encoding candidates",
                    query_emb.shape[0],
                    target_embs.shape[1],
                )
                invalidate_embedding_cache(
                    reason=(
                        f"dim mismatch query={query_emb.shape[0]} "
                        f"cand={target_embs.shape[1]}"
                    )
                )
                target_embs = encode_texts_cached(model, candidate_questions)
                if query_emb.shape[0] != target_embs.shape[1]:
                    # 再エンコード後も不一致は callerレベルの不整合 → degrade
                    return None

            scores = _cosine_similarity_batch(query_emb, target_embs)
        except Exception as e:  # noqa: BLE001
            _logger.warning("GoldenStore SBert rerank failed: %s", e)
            return None

        reranked = [
            (float(score), entry)
            for score, (_, entry) in zip(scores, candidates)
        ]
        reranked.sort(key=lambda x: x[0], reverse=True)
        return reranked

    def list_keys(self):
        return list(self._store.keys())
