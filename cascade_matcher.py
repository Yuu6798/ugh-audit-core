"""cascade_matcher.py — cascade Tier 2 候補生成

SBert embedding による命題×response の文レベルマッチング。
detector.py の既存ロジックは一切変更しない。
判定層（ugh_calculator 等）から呼ばれる補助モジュール。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import numpy as np

# --- SBert optional import ---
try:
    from sentence_transformers import SentenceTransformer
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False

# --- パラメータ ---
THETA_SBERT: float = 0.50   # cosine similarity 閾値（dev_cascade_20 で校正済み）
DELTA_GAP: float = 0.04     # top1 - top2 ギャップ閾値（dev_cascade_20 で校正済み）
MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"

# 括弧保護用プレースホルダ
_PAREN_PLACEHOLDER = "\x00PERIOD\x00"


def load_model(model_name: str = MODEL_NAME) -> SentenceTransformer:
    """SentenceTransformer モデルをロードする。

    Args:
        model_name: HuggingFace モデル名。

    Returns:
        SentenceTransformer インスタンス。

    Raises:
        ImportError: sentence-transformers がインストールされていない場合。
    """
    if not _HAS_SBERT:
        raise ImportError(
            "sentence-transformers is required for cascade Tier 2. "
            "Install with: pip install sentence-transformers"
        )
    return SentenceTransformer(model_name)


def encode_texts(
    model: SentenceTransformer,
    texts: List[str],
    batch_size: int = 64,
) -> np.ndarray:
    """テキストリストを batch encoding する。

    Args:
        model: SentenceTransformer インスタンス。
        texts: エンコード対象のテキストリスト。
        batch_size: バッチサイズ。

    Returns:
        (N, D) の numpy 配列。各行が1テキストの embedding。
    """
    return model.encode(texts, batch_size=batch_size, convert_to_numpy=True)


def split_response(response: str) -> List[str]:
    """response を文/節に分割する。

    分割ルール:
    1. 改行で分割（暗黙の文境界）
    2. 括弧内の句点を保護
    3. 句点「。」で分割
    4. 80字超の文は読点「、」でさらに分割を試行
    5. 空文字列・空白のみは除外
    6. 前後空白を strip

    Args:
        response: AI回答の全文。

    Returns:
        文/節のリスト。
    """
    if not response or not response.strip():
        return []

    # Step 1: 改行で分割
    lines = response.split("\n")

    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Step 2: 括弧内の句点を保護
        protected = _protect_paren_periods(line)

        # Step 3: 句点で分割
        sentences = protected.split("。")

        for sent in sentences:
            # プレースホルダを復元
            sent = sent.replace(_PAREN_PLACEHOLDER, "。").strip()
            if not sent:
                continue

            # Step 4: 80字超は読点で分割を試行
            if len(sent) > 80:
                clauses = _split_by_comma(sent)
                result.extend(clauses)
            else:
                result.append(sent)

    return result


def _protect_paren_periods(text: str) -> str:
    """括弧内の句点をプレースホルダに置換する。"""
    # 全角括弧
    text = re.sub(
        r"（[^）]*?）",
        lambda m: m.group(0).replace("。", _PAREN_PLACEHOLDER),
        text,
    )
    # 半角括弧
    text = re.sub(
        r"\([^)]*?\)",
        lambda m: m.group(0).replace("。", _PAREN_PLACEHOLDER),
        text,
    )
    return text


def _split_by_comma(text: str) -> List[str]:
    """80字超の文を読点「、」で分割する。

    分割後に空・空白のみの要素は除外する。
    分割しても全パーツが短くならない場合はそのまま返す。
    """
    parts = text.split("、")
    if len(parts) <= 1:
        return [text]

    # 読点で分割した各部分を結合して適度な長さにする
    # 単純分割（各パーツを独立節として扱う）
    result = []
    for p in parts:
        p = p.strip()
        if p:
            result.append(p)
    return result if result else [text]


def tier2_candidate(
    proposition: str,
    response: str,
    model: SentenceTransformer,
    theta: float = THETA_SBERT,
    delta: float = DELTA_GAP,
) -> Dict:
    """response を文/節に分割し、proposition との cosine similarity を計算。

    Args:
        proposition: 命題テキスト。
        response: AI回答全文。
        model: SentenceTransformer インスタンス。
        theta: cosine similarity 閾値。
        delta: top1 - top2 ギャップ閾値。

    Returns:
        {
            "top1_sentence": str,
            "top1_score": float,
            "top2_score": float,
            "gap": float,
            "all_scores": list[float],
            "pass_tier2": bool,
        }
    """
    segments = split_response(response)
    if not segments:
        return {
            "top1_sentence": "",
            "top1_score": 0.0,
            "top2_score": 0.0,
            "gap": 0.0,
            "all_scores": [],
            "pass_tier2": False,
        }

    # Encode proposition + all segments in one batch
    all_texts = [proposition] + segments
    embeddings = encode_texts(model, all_texts)

    prop_emb = embeddings[0]  # (D,)
    seg_embs = embeddings[1:]  # (N, D)

    # Cosine similarity
    scores = _cosine_similarity_batch(prop_emb, seg_embs)

    # Sort descending
    sorted_indices = np.argsort(scores)[::-1]
    top1_idx = sorted_indices[0]
    top1_score = float(scores[top1_idx])
    top1_sentence = segments[top1_idx]

    top2_score = float(scores[sorted_indices[1]]) if len(sorted_indices) > 1 else 0.0
    gap = top1_score - top2_score

    pass_tier2 = (top1_score >= theta) and (gap >= delta)

    return {
        "top1_sentence": top1_sentence,
        "top1_score": round(top1_score, 4),
        "top2_score": round(top2_score, 4),
        "gap": round(gap, 4),
        "all_scores": [round(float(s), 4) for s in scores],
        "pass_tier2": pass_tier2,
    }


def _cosine_similarity_batch(query: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """query (D,) と targets (N, D) のコサイン類似度を計算。"""
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    targets_norm = targets / (np.linalg.norm(targets, axis=1, keepdims=True) + 1e-10)
    return targets_norm @ query_norm
