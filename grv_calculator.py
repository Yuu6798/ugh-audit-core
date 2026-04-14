"""grv_calculator.py — 因果構造損失 (grv) 計算モジュール v1.3

回答が問いの重力圏からどれだけ逸脱しているかを計測する。

合成式 (2成分):
  grv = normalize(w_d * drift + w_s * dispersion)

成分:
  drift      = 1 - max(0, raw_cosine(G_res, G_ref))  # 重心逸脱 (生コサイン)
  dispersion = mean(1 - cos01(s_i, G_res))            # 内部散漫度
  collapse   = 診断出力のみ (合成値に含めない)         # 偏在集中度

collapse は HA48 で増分寄与マイナス (ρ 悪化) が確認されたため、
合成値から除外。「良い集中」と「悪い集中」の弁別には質的に異なる
計測が必要であり、collapse v2 として独立した設計タスクに分離する。

SBert 依存。SBert 未導入時は None を返す (L_sem で L_G=None → 除外)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

# --- 確定重み (HA48 ρ=-0.318 で検証済み) ---
# collapse は増分寄与マイナスのため合成値から除外。診断出力のみ。
W_DRIFT = 0.60
W_DISPERSION = 0.10
W_COLLAPSE = 0.00

# --- 暫定タグ閾値 ---
TAG_HIGH = 0.66
TAG_MID = 0.33

# --- collapse シャープ化の温度パラメータ ---
TAU = 0.1

# --- 参照重心の重み ---
_REF_WEIGHTS = {
    "manual":   {"w_q": 0.60, "w_m": 0.40, "ref_confidence": 1.00},
    "auto":     {"w_q": 0.80, "w_m": 0.20, "ref_confidence": 0.70},
    "missing":  {"w_q": 1.00, "w_m": 0.00, "ref_confidence": 0.50},
}

GRV_VERSION = "v1.3"
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

    v1.3: 生コサイン [-1,1] を使い、cos01 の圧縮を回避。
    負のコサインは drift=1.0 にクランプ。

    Returns:
        (drift, raw_cosine)
    """
    raw_cos = _cos_sim(G_res, G_ref)
    drift = _clamp(1.0 - max(0.0, raw_cos))
    return drift, raw_cos


def compute_dispersion(sentence_vecs: np.ndarray, G_res: np.ndarray) -> float:
    """内部散漫度: 0 = まとまっている, 1 = 散漫

    1文以下の場合は 0.0 を返す (散漫さが定義不能)。
    dispersion は cos01 を維持 (参照非依存の内部指標のため圧縮が問題にならない)。
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
    tau: float = TAU,
) -> tuple[float, bool, List[float], List[float]]:
    """偏在集中度: 0 = 均等分布, 1 = 一点集中

    v1.3: τ パラメータで親和度をシャープ化してからエントロピーを計算。

    Returns:
        (collapse, collapse_applicable, raw_affinities, sharp_affinities)
    """
    n_props = len(prop_vecs)
    if n_props < 2:
        return 0.0, False, [], []

    # 各命題への最大親和度 (重み付き)
    raw_a_k = []
    for k in range(n_props):
        max_sim = max(cos01(s, prop_vecs[k]) for s in sentence_vecs) if len(sentence_vecs) > 0 else 0.0
        raw_a_k.append(prop_weights[k] * max_sim)

    total_raw = sum(raw_a_k)
    if total_raw == 0.0:
        return 0.0, False, raw_a_k, raw_a_k

    # τ シャープ化 (tau は正の値でなければならない)
    if tau <= 0:
        tau = TAU
    sharp_a_k = [a ** (1.0 / tau) if a > 0 else 0.0 for a in raw_a_k]
    total_sharp = sum(sharp_a_k)
    if total_sharp == 0.0:
        return 0.0, False, raw_a_k, sharp_a_k

    # 正規化エントロピー
    p_dist = [a / total_sharp for a in sharp_a_k]
    entropy = -sum(p * math.log(p) if p > 0 else 0.0 for p in p_dist)
    max_entropy = math.log(len(p_dist))
    if max_entropy == 0.0:
        return 0.0, True, raw_a_k, sharp_a_k

    collapse = _clamp(1.0 - entropy / max_entropy)
    return collapse, True, raw_a_k, sharp_a_k


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
    tau: float
    prop_weights: List[float]
    prop_affinity_raw: List[float] = field(default_factory=list)
    prop_affinity_sharp: List[float] = field(default_factory=list)
    drift_raw_cosine: float = 0.0
    ref_w_q: float = 0.0
    ref_w_m: float = 0.0
    grv_tag: str = "low_gravity"


def _grv_tag_from_value(grv: float) -> str:
    """暫定タグ分類"""
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
    tau: float = TAU,
    w_drift: float = W_DRIFT,
    w_dispersion: float = W_DISPERSION,
) -> Optional[GrvResult]:
    """grv を計算する。SBert 未導入時は None を返す。

    Args:
        question: ユーザーの質問
        response_text: AI の回答
        question_meta: 問題メタデータ (core_propositions 等)
        metadata_source: メタデータ出所
        tau: collapse シャープ化温度 (デフォルト: 0.1)
        w_drift: drift 重み
        w_dispersion: dispersion 重み
        w_collapse: collapse 重み
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

    weights = _REF_WEIGHTS[source_key]
    w_q = weights["w_q"]
    w_m = weights["w_m"]
    ref_confidence = weights["ref_confidence"]
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
    prop_sources: List[str] = []

    if question_meta:
        core_props = question_meta.get("core_propositions", [])
        for p in core_props:
            if isinstance(p, str) and p.strip():
                propositions.append(p.strip())
                prop_sources.append("manual_core" if source_key == "manual" else "meta_derived")
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

    # --- 3成分計算 ---
    # drift: v1.3 — 生コサインベース
    drift, drift_raw_cosine = compute_drift(G_res, G_ref)

    # dispersion: cos01 を維持
    dispersion = compute_dispersion(sent_vecs, G_res)

    # collapse: v1.3 — τ シャープ化
    prop_weight_list: List[float] = []
    prop_affinity_raw: List[float] = []
    prop_affinity_sharp: List[float] = []
    if propositions:
        prop_vecs = encode_texts(model, propositions)
        for src in prop_sources:
            prop_weight_list.append(1.0 if src == "manual_core" else meta_scale)
        collapse_raw, collapse_applicable, aff_raw, aff_sharp = compute_collapse(
            sent_vecs, prop_vecs, prop_weight_list, tau=tau,
        )
        prop_affinity_raw = [round(a, 4) for a in aff_raw]
        prop_affinity_sharp = [round(a, 4) for a in aff_sharp]
        # auto-meta 時は collapse を meta_scale で追加減衰
        collapse = _clamp(collapse_raw * meta_scale) if source_key != "manual" else collapse_raw
    else:
        collapse = 0.0
        collapse_applicable = False

    # --- 合成 (2成分: drift + dispersion) ---
    # collapse は HA48 で増分寄与マイナスのため合成値から除外。診断出力のみ。
    w_total = w_drift + w_dispersion
    if w_total > 0:
        grv = _clamp((w_drift / w_total) * drift + (w_dispersion / w_total) * dispersion)
    else:
        grv = 0.0

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
        tau=tau,
        prop_weights=[round(w, 4) for w in prop_weight_list],
        prop_affinity_raw=prop_affinity_raw,
        prop_affinity_sharp=prop_affinity_sharp,
        drift_raw_cosine=round(drift_raw_cosine, 4),
        ref_w_q=w_q,
        ref_w_m=w_m,
        grv_tag=_grv_tag_from_value(grv),
    )
