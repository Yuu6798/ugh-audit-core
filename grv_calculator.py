"""grv_calculator.py — 因果構造損失 (grv) 計算モジュール v1.4

回答が問いの重力圏からどれだけ逸脱しているかを計測する。

合成式:
  grv = clamp(w_d * drift + w_s * dispersion + w_c * collapse_v2)

成分:
  drift       = 1 - max(0, raw_cosine(G_res, G_ref))       # 重心逸脱 (生コサイン)
  dispersion  = mean(1 - cos01(s_i, G_res))                 # 内部散漫度
  collapse_v2 = mean(1 - max_affinity(u_i, propositions))   # 残留型偏在集中度

補助計測:
  cover_soft  = mean(max_affinity(p_k, units))              # 命題→応答の連続到達度
  wash_index  = collapse_v2 × cover_soft                    # 表面到達 + 内容空洞
  wash_index_c = collapse_v2 × C_normalized                 # C ベース wash

SBert 依存。SBert 未導入時は None を返す (L_sem で L_G=None → 除外)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

# --- 確定重み (HA48 ρ=-0.357, σ=0.051 で検証済み) ---
W_DRIFT = 0.70
W_DISPERSION = 0.05
W_COLLAPSE_V2 = 0.25

# --- 暫定タグ閾値 ---
TAG_HIGH = 0.66
TAG_MID = 0.33

# --- 参照重心の重み ---
_REF_WEIGHTS = {
    "manual":   {"w_q": 0.60, "w_m": 0.40, "ref_confidence": 1.00},
    "auto":     {"w_q": 0.80, "w_m": 0.20, "ref_confidence": 0.70},
    "missing":  {"w_q": 1.00, "w_m": 0.00, "ref_confidence": 0.50},
}

GRV_VERSION = "v1.4"
EMBEDDING_BACKEND = "paraphrase-multilingual-MiniLM-L12-v2"


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


def compute_drift(G_res: np.ndarray, G_ref: np.ndarray) -> tuple[float, float]:
    """重心逸脱: 0 = 問いの重力圏内, 1 = 逸脱

    Returns:
        (drift, raw_cosine)
    """
    raw_cos = _cos_sim(G_res, G_ref)
    drift = _clamp(1.0 - max(0.0, raw_cos))
    return drift, raw_cos


def compute_dispersion(sentence_vecs: np.ndarray, G_res: np.ndarray) -> float:
    """内部散漫度: 0 = まとまっている, 1 = 散漫"""
    n = len(sentence_vecs)
    if n <= 1:
        return 0.0
    total = sum(1.0 - cos01(s, G_res) for s in sentence_vecs)
    return _clamp(total / n)


def compute_collapse_v2(
    sentence_vecs: np.ndarray,
    prop_vecs: np.ndarray,
) -> tuple[float, bool, List[float]]:
    """残留型偏在集中度: 命題で説明できない残留が多いほど高い

    0 = 全文が命題で説明できる, 1 = 大半が命題と無関係

    Returns:
        (collapse_v2, applicable, per_sentence_max_affinity)
    """
    if len(sentence_vecs) == 0 or len(prop_vecs) == 0:
        return 0.0, False, []

    aff_per_sent = []
    for s in sentence_vecs:
        max_aff = max(cos01(s, p) for p in prop_vecs)
        aff_per_sent.append(max_aff)

    collapse_v2 = _clamp(sum(1.0 - a for a in aff_per_sent) / len(aff_per_sent))
    return collapse_v2, True, aff_per_sent


def compute_cover_soft(
    sentence_vecs: np.ndarray,
    prop_vecs: np.ndarray,
) -> tuple[float, List[float]]:
    """命題→応答の連続到達度

    0 = 命題のどれにも回答が届いていない, 1 = 全命題に強く届いている

    Returns:
        (cover_soft, per_proposition_max_affinity)
    """
    if len(prop_vecs) == 0 or len(sentence_vecs) == 0:
        return 0.0, []

    cover_per_prop = []
    for p in prop_vecs:
        max_aff = max(cos01(p, s) for s in sentence_vecs)
        cover_per_prop.append(max_aff)

    cover_soft = sum(cover_per_prop) / len(cover_per_prop)
    return _clamp(cover_soft), cover_per_prop


@dataclass
class GrvResult:
    """grv v1.4 計算結果"""
    grv: float
    drift: float
    dispersion: float
    collapse_v2: float
    collapse_v2_applicable: bool
    cover_soft: float
    wash_index: float
    wash_index_c: float
    n_sentences: int
    n_propositions: int
    meta_source: str
    ref_confidence: float
    drift_raw_cosine: float
    weights: dict
    prop_affinity_per_sentence: List[float] = field(default_factory=list)
    cover_soft_per_proposition: List[float] = field(default_factory=list)
    grv_tag: str = "low_gravity"


def _grv_tag_from_value(grv: float) -> str:
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
    w_drift: float = W_DRIFT,
    w_dispersion: float = W_DISPERSION,
    w_collapse_v2: float = W_COLLAPSE_V2,
    c_normalized: Optional[float] = None,
) -> Optional[GrvResult]:
    """grv を計算する。SBert 未導入時は None を返す。

    Args:
        question: ユーザーの質問
        response_text: AI の回答
        question_meta: 問題メタデータ (core_propositions 等)
        metadata_source: メタデータ出所
        w_drift: drift 重み
        w_dispersion: dispersion 重み
        w_collapse_v2: collapse_v2 重み
        c_normalized: C (hit/total, 0-1) — wash_index_c 計算用
    """
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

    weights_cfg = _REF_WEIGHTS[source_key]
    w_q = weights_cfg["w_q"]
    w_m = weights_cfg["w_m"]
    ref_confidence = weights_cfg["ref_confidence"]
    meta_scale = _clamp(2.0 * ref_confidence - 1.0)

    # --- 文分割 ---
    response_sentences = _split_sentences(response_text)
    if not response_sentences:
        response_sentences = [response_text]

    # --- 埋め込み計算 ---
    sent_vecs = encode_texts(model, response_sentences)
    G_res = np.mean(sent_vecs, axis=0)

    question_units = _split_sentences(question)
    if not question_units:
        question_units = [question]
    q_vecs = encode_texts(model, question_units)
    G_q = np.mean(q_vecs, axis=0)

    meta_units: List[str] = []
    propositions: List[str] = []

    if question_meta:
        for p in question_meta.get("core_propositions", []):
            if isinstance(p, str) and p.strip():
                propositions.append(p.strip())
                meta_units.append(p.strip())
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

    # --- 成分計算 ---
    drift, drift_raw_cosine = compute_drift(G_res, G_ref)
    dispersion = compute_dispersion(sent_vecs, G_res)

    # collapse_v2 (residual 型) + cover_soft
    if propositions:
        prop_vecs = encode_texts(model, propositions)
        collapse_v2, collapse_v2_applicable, aff_per_sent = compute_collapse_v2(
            sent_vecs, prop_vecs,
        )
        cover_soft, cover_per_prop = compute_cover_soft(sent_vecs, prop_vecs)
    else:
        prop_vecs = np.array([])
        collapse_v2 = 0.0
        collapse_v2_applicable = False
        aff_per_sent = []
        cover_soft = 0.0
        cover_per_prop = []

    # wash_index
    wash_index = _clamp(collapse_v2 * cover_soft)
    c_val = c_normalized if c_normalized is not None else 0.0
    wash_index_c = _clamp(collapse_v2 * c_val)

    # --- 合成 ---
    if collapse_v2_applicable and w_collapse_v2 > 0:
        w_total = w_drift + w_dispersion + w_collapse_v2
        grv = _clamp(
            (w_drift / w_total) * drift
            + (w_dispersion / w_total) * dispersion
            + (w_collapse_v2 / w_total) * collapse_v2
        )
    else:
        w_total = w_drift + w_dispersion
        grv = _clamp((w_drift / w_total) * drift + (w_dispersion / w_total) * dispersion) if w_total > 0 else 0.0

    return GrvResult(
        grv=round(grv, 4),
        drift=round(drift, 4),
        dispersion=round(dispersion, 4),
        collapse_v2=round(collapse_v2, 4),
        collapse_v2_applicable=collapse_v2_applicable,
        cover_soft=round(cover_soft, 4),
        wash_index=round(wash_index, 4),
        wash_index_c=round(wash_index_c, 4),
        n_sentences=len(response_sentences),
        n_propositions=len(propositions),
        meta_source=source_key,
        ref_confidence=ref_confidence,
        drift_raw_cosine=round(drift_raw_cosine, 4),
        weights={"w_d": w_drift, "w_s": w_dispersion, "w_c": w_collapse_v2},
        prop_affinity_per_sentence=[round(a, 4) for a in aff_per_sent],
        cover_soft_per_proposition=[round(a, 4) for a in cover_per_prop],
        grv_tag=_grv_tag_from_value(grv),
    )
