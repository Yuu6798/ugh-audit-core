"""grv_calculator.py — 因果構造損失 (grv) 計算モジュール

回答が問いの重力圏からどれだけ逸脱しているかを3成分で計測する:
  drift      = 1 - cos01(G_res, G_ref)     # 重心逸脱
  dispersion = mean(1 - cos01(s_i, G_res))  # 内部散漫度
  collapse   = 1 - H(p_k) / log(K)         # 偏在集中度

grv = clamp(w_d * drift + w_s * dispersion + w_c * collapse, 0, 1)

SBert 依存。SBert 未導入時は None を返す (L_sem で L_G=None → 除外)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

# --- 暫定重み (Phase 2 で HA48 実値に基づき再校正) ---
W_DRIFT = 0.50
W_DISPERSION = 0.20
W_COLLAPSE = 0.30

# --- 暫定タグ閾値 (Phase 2 で再校正) ---
TAG_HIGH = 0.66
TAG_MID = 0.33

# --- 参照重心の重み ---
_REF_WEIGHTS = {
    "manual":   {"w_q": 0.60, "w_m": 0.40, "ref_confidence": 1.00},
    "auto":     {"w_q": 0.80, "w_m": 0.20, "ref_confidence": 0.70},
    "missing":  {"w_q": 1.00, "w_m": 0.00, "ref_confidence": 0.50},
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _split_sentences(text: str) -> List[str]:
    """テキストを文単位に分割する (日本語 + 英語対応)"""
    parts = re.split(r'[。．！？!?.\n]+', text)
    return [p.strip() for p in parts if p.strip()]


def _cos_sim(a: np.ndarray, b: np.ndarray) -> float:
    """コサイン類似度 [-1, 1]"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cos01(a: np.ndarray, b: np.ndarray) -> float:
    """コサイン類似度を [0, 1] に正規化"""
    return (_cos_sim(a, b) + 1.0) / 2.0


def compute_drift(G_res: np.ndarray, G_ref: np.ndarray) -> float:
    """重心逸脱: 0 = 問いの重力圏内, 1 = 逸脱"""
    return _clamp(1.0 - cos01(G_res, G_ref))


def compute_dispersion(sentence_vecs: np.ndarray, G_res: np.ndarray) -> float:
    """内部散漫度: 0 = まとまっている, 1 = 散漫

    1文以下の場合は 0.0 を返す (散漫さが定義不能)。
    """
    n = len(sentence_vecs)
    if n <= 1:
        return 0.0
    total = sum(1.0 - cos01(s, G_res) for s in sentence_vecs)
    return _clamp(total / n)


def compute_collapse(
    sentence_vecs: np.ndarray,
    prop_vecs: np.ndarray,
    prop_weights: List[float],
) -> tuple[float, bool]:
    """偏在集中度: 0 = 均等分布, 1 = 一点集中

    命題が2未満の場合は (0.0, False) を返す (偏在が定義不能)。

    Returns:
        (collapse, collapse_applicable)
    """
    n_props = len(prop_vecs)
    if n_props < 2:
        return 0.0, False

    # 各命題への最大親和度 (重み付き)
    a_k = []
    for k in range(n_props):
        max_sim = max(cos01(s, prop_vecs[k]) for s in sentence_vecs) if len(sentence_vecs) > 0 else 0.0
        a_k.append(prop_weights[k] * max_sim)

    total = sum(a_k)
    if total == 0.0:
        return 0.0, True

    # 正規化エントロピー
    p_dist = [a / total for a in a_k]
    entropy = -sum(p * math.log(p) if p > 0 else 0.0 for p in p_dist)
    max_entropy = math.log(len(p_dist))
    if max_entropy == 0.0:
        return 0.0, True

    collapse = _clamp(1.0 - entropy / max_entropy)
    return collapse, True


@dataclass
class GrvResult:
    """grv 計算結果"""
    grv: float
    drift: float
    dispersion: float
    collapse: float
    collapse_applicable: bool
    n_sentences: int
    n_propositions: int
    meta_source: str
    ref_confidence: float
    meta_scale: float
    prop_weights: List[float]
    prop_affinity: List[float] = field(default_factory=list)
    grv_tag: str = "low_gravity"


def _grv_tag_from_value(grv: float) -> str:
    """暫定タグ分類 (Phase 2 で再校正)"""
    if grv >= TAG_HIGH:
        return "high_gravity"
    if grv >= TAG_MID:
        return "mid_gravity"
    return "low_gravity"


def compute_grv(
    *,
    question: str,
    response_text: str,
    question_meta: Optional[dict] = None,
    metadata_source: str = "missing",
) -> Optional[GrvResult]:
    """grv を計算する。SBert 未導入時は None を返す。

    Args:
        question: ユーザーの質問
        response_text: AI の回答
        question_meta: 問題メタデータ (core_propositions 等)
        metadata_source: メタデータ出所 ("inline"/"manual" → manual, "llm_generated"/"auto" → auto, else → missing)
    """
    # SBert ロード (cascade_matcher のシングルトンを再利用)
    try:
        from cascade_matcher import encode_texts, get_shared_model
    except ImportError:
        return None

    model = get_shared_model()
    if model is None:
        return None

    # --- メタソース判定 ---
    if metadata_source in ("inline", "manual", "golden_store"):
        source_key = "manual"
    elif metadata_source in ("llm_generated", "auto", "computed_ai_draft"):
        source_key = "auto"
    else:
        source_key = "missing"

    weights = _REF_WEIGHTS[source_key]
    w_q = weights["w_q"]
    w_m = weights["w_m"]
    ref_confidence = weights["ref_confidence"]
    meta_scale = _clamp(2.0 * ref_confidence - 1.0)

    # --- 文分割 ---
    response_sentences = _split_sentences(response_text)
    if not response_sentences:
        response_sentences = [response_text]  # フォールバック: 全文を1文として扱う

    # --- 埋め込み計算 ---
    # 応答文
    sent_vecs = encode_texts(model, response_sentences)
    G_res = np.mean(sent_vecs, axis=0)

    # 質問
    question_units = _split_sentences(question)
    if not question_units:
        question_units = [question]
    q_vecs = encode_texts(model, question_units)
    G_q = np.mean(q_vecs, axis=0)

    # メタデータ (核心命題 + 質問テキスト)
    meta_units: List[str] = []
    propositions: List[str] = []
    prop_sources: List[str] = []

    if question_meta:
        core_props = question_meta.get("core_propositions", [])
        for p in core_props:
            if isinstance(p, str) and p.strip():
                propositions.append(p.strip())
                prop_sources.append("manual_core" if source_key == "manual" else "meta_derived")
                meta_units.append(p.strip())
        # acceptable_variants もメタ情報として利用
        for v in question_meta.get("acceptable_variants", []):
            if isinstance(v, str) and v.strip():
                meta_units.append(v.strip())

    # --- 参照重心 G_ref ---
    if meta_units and meta_scale > 0:
        m_vecs = encode_texts(model, meta_units)
        G_m = np.mean(m_vecs, axis=0)
        G_ref_raw = w_q * G_q + (meta_scale * w_m) * G_m
    else:
        G_ref_raw = G_q.copy()

    norm = np.linalg.norm(G_ref_raw)
    G_ref = G_ref_raw / norm if norm > 1e-10 else G_ref_raw

    # --- 3成分計算 ---
    drift = compute_drift(G_res, G_ref)
    dispersion = compute_dispersion(sent_vecs, G_res)

    # collapse
    prop_weights: List[float] = []
    if propositions:
        prop_vecs = encode_texts(model, propositions)
        for src in prop_sources:
            prop_weights.append(1.0 if src == "manual_core" else meta_scale)
        collapse_raw, collapse_applicable = compute_collapse(sent_vecs, prop_vecs, prop_weights)
        # auto-meta 時は collapse 自体を meta_scale で減衰
        # (均一重みの正規化で打ち消される問題への対処)
        collapse = _clamp(collapse_raw * meta_scale) if source_key != "manual" else collapse_raw
    else:
        prop_vecs = np.array([])
        collapse = 0.0
        collapse_applicable = False

    # --- 合成 ---
    if collapse_applicable:
        grv = _clamp(W_DRIFT * drift + W_DISPERSION * dispersion + W_COLLAPSE * collapse)
    else:
        # collapse 非適用時は drift + dispersion のみで重み再配分
        w_total = W_DRIFT + W_DISPERSION
        grv = _clamp((W_DRIFT / w_total) * drift + (W_DISPERSION / w_total) * dispersion)

    # --- debug: 命題別親和度 ---
    prop_affinity: List[float] = []
    if propositions and len(prop_vecs) > 0:
        for k in range(len(propositions)):
            if len(sent_vecs) > 0:
                max_sim = max(cos01(s, prop_vecs[k]) for s in sent_vecs)
            else:
                max_sim = 0.0
            prop_affinity.append(round(max_sim, 4))

    return GrvResult(
        grv=round(grv, 4),
        drift=round(drift, 4),
        dispersion=round(dispersion, 4),
        collapse=round(collapse, 4),
        collapse_applicable=collapse_applicable,
        n_sentences=len(response_sentences),
        n_propositions=len(propositions),
        meta_source=source_key,
        ref_confidence=ref_confidence,
        meta_scale=round(meta_scale, 4),
        prop_weights=[round(w, 4) for w in prop_weights],
        prop_affinity=prop_affinity,
        grv_tag=_grv_tag_from_value(grv),
    )
