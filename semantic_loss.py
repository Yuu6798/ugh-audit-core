"""semantic_loss.py — 意味損失関数 L_sem (Phase 1-4)

Evidence から L_P, L_Q, L_X, L_R, L_A, L_F を算出し、
オプショナルな grv 値から L_G を導出する。

デフォルト重みは HA48 (n=48) で Spearman ρ 最大化により校正済み。
- 現行 ΔE: ρ = -0.5195
- L_sem (HA48 最適化): ρ = -0.5563

参照: docs/semantic_loss.md, analysis/optimize_semantic_loss_weights.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ugh_calculator import Evidence

# --- オプショナル依存: detector (L_X 算出に使用) ---
try:
    from detector import detect_operator, OPERATOR_CATALOG
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False


# --- デフォルト重み (Phase 4: HA48 校正済み) ---
# HA48 最適化結果 (f2 込み): L_P=0.375, L_R=0.125, L_F=0.50 (ρ=-0.5563)
# 最適化で L_Q=0, L_A=0 となったが、理論的完全性のため小さな重みを残す。
# L_G, L_X は HA48 データに含まれないため理論ベースの重みを維持。
DEFAULT_WEIGHTS: Dict[str, float] = {
    "L_P": 0.25,   # 命題損失 (HA48 最適化 → コア重み)
    "L_Q": 0.02,   # 制約損失 (HA48 で信号弱、理論的保持)
    "L_R": 0.08,   # 参照安定性 (HA48 最適化)
    "L_A": 0.02,   # 曖昧性増大 (HA48 で信号なし、理論的保持)
    "L_G": 0.13,   # 因果構造 (HA48 外、理論ベース)
    "L_F": 0.35,   # 用語捏造 (HA48 で最強信号)
    "L_X": 0.15,   # 極性反転 (HA48 外、理論ベース)
}


@dataclass(frozen=True)
class SemanticLoss:
    """意味損失関数の各項と合計"""

    L_P: Optional[float]          # 命題損失 [0,1]
    L_Q: float                    # 制約損失 [0,1]
    L_R: Optional[float]          # 参照安定性損失 [0,1]
    L_A: float                    # 曖昧性増大損失 [0,1]
    L_G: Optional[float]          # 因果構造損失 [0,1] (grv 統合)
    L_F: float                    # 用語捏造損失 [0,1] (f2 由来, Phase 4)
    L_X: Optional[float]          # 極性反転損失 [0,1]
    L_total: Optional[float]      # 利用可能な項の重み付き合計 [0,1]
    weights_used: Dict[str, float] = field(default_factory=dict)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _compute_L_P(evidence: Evidence) -> Optional[float]:
    """L_P = 1 - C = 1 - (hits / total)

    命題未提供 (total=0) 時は None。
    """
    if evidence.propositions_total == 0:
        return None
    return _clamp(1.0 - evidence.propositions_hit / evidence.propositions_total)


def _compute_L_Q(evidence: Evidence) -> float:
    """L_Q = f3_operator (演算子制約の未処理度)

    f3 = 0.0 → 制約を適切に処理, 1.0 → 完全に未処理。
    """
    return _clamp(evidence.f3_operator)


def _compute_L_R(evidence: Evidence) -> Optional[float]:
    """L_R = f4_premise (前提受容 → 参照安定性損失)

    f4 = 0.0 → 前提を適切に扱った, 0.5 → 部分的受容, 1.0 → 完全受容。
    f4 = None → 未計算 (trap_type なし等)。
    """
    if evidence.f4_premise is None:
        return None
    return _clamp(evidence.f4_premise)


def _compute_L_A(evidence: Evidence) -> float:
    """L_A = f1_anchor (主題逸脱 → 曖昧性増大)

    f1 = 0.0 → 主題に沿った回答, 0.5 → 部分逸脱, 1.0 → 完全逸脱。
    主題逸脱は「何について回答しているか」の曖昧性を増大させる。
    """
    return _clamp(evidence.f1_anchor)


def _compute_L_F(evidence: Evidence) -> float:
    """L_F = f2_unknown (用語捏造損失)

    f2 = 0.0 → 捏造なし, 0.5 → 部分的, 1.0 → 完全な用語捏造。
    Phase 4 の HA48 校正で最強の単独予測子と判明 (ρ=-0.3853)。
    L_P (命題欠落) とは独立した「偽命題の混入」を捉える。
    """
    return _clamp(evidence.f2_unknown)


def _compute_L_X(
    evidence: Evidence,
    propositions: Optional[List[str]],
) -> Optional[float]:
    """L_X = polarity_flip 命題の miss 率

    各 miss 命題に detect_operator() を適用し、
    effect == "polarity_flip" の命題が miss された割合を返す。
    命題テキストが未提供の場合は None。
    """
    if evidence.propositions_total == 0 or not propositions:
        return None
    if not _HAS_DETECTOR:
        return None

    polarity_misses = 0
    for idx in evidence.miss_ids:
        if idx < len(propositions):
            op = detect_operator(propositions[idx])
            if (
                op is not None
                and OPERATOR_CATALOG[op.family]["effect"] == "polarity_flip"
            ):
                polarity_misses += 1
    return _clamp(polarity_misses / evidence.propositions_total)


def _compute_L_G(grv: Optional[float]) -> Optional[float]:
    """L_G = grv (語彙重力 → 因果構造損失)

    engine の compute_grv() が算出した grv 値を直接使用する。
    grv = 0.0 → 構造的に安定, 1.0 → 構造的逸脱。
    grv 未提供時は None。

    engine の grv 計算式:
        grv = clamp(beta * (1 - entropy_ratio) + (1 - beta) * (1 - centroid_cosine))
    """
    if grv is None:
        return None
    return _clamp(grv)


def _weighted_total(
    components: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Tuple[Optional[float], Dict[str, float]]:
    """非 None 項の重み付き合計と正規化後の重みを返す"""
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None, {}

    w_sum = sum(weights.get(k, 0.0) for k in available)
    if w_sum == 0.0:
        return None, {}

    normalized = {k: weights.get(k, 0.0) / w_sum for k in available}
    total = sum(normalized[k] * available[k] for k in available)
    return _clamp(total), normalized


def compute_semantic_loss(
    evidence: Evidence,
    *,
    propositions: Optional[List[str]] = None,
    grv: Optional[float] = None,
    weights: Optional[Dict[str, float]] = None,
) -> SemanticLoss:
    """Evidence から意味損失関数を算出する (Phase 1-4)

    Phase 1: L_P, L_Q, L_X
    Phase 2: L_R (f4 → 参照安定性), L_A (f1 → 曖昧性増大)
    Phase 3: L_G (grv → 因果構造損失)
    Phase 4: L_F (f2 → 用語捏造損失), 重み HA48 校正

    Args:
        evidence: 検出層の出力
        propositions: 命題テキストのリスト (L_X 算出に必要)
        grv: engine の compute_grv() が算出した語彙重力値 [0,1] (L_G 算出に必要)
        weights: 各項の重み (未指定時は DEFAULT_WEIGHTS)
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS

    L_P = _compute_L_P(evidence)
    L_Q = _compute_L_Q(evidence)
    L_R = _compute_L_R(evidence)
    L_A = _compute_L_A(evidence)
    L_G = _compute_L_G(grv)
    L_F = _compute_L_F(evidence)
    L_X = _compute_L_X(evidence, propositions)

    components = {
        "L_P": L_P, "L_Q": L_Q, "L_R": L_R,
        "L_A": L_A, "L_G": L_G, "L_F": L_F, "L_X": L_X,
    }
    L_total, weights_used = _weighted_total(components, w)

    return SemanticLoss(
        L_P=L_P,
        L_Q=L_Q,
        L_R=L_R,
        L_A=L_A,
        L_G=L_G,
        L_F=L_F,
        L_X=L_X,
        L_total=L_total,
        weights_used=weights_used,
    )
