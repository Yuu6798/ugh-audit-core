"""semantic_loss.py — 意味損失関数 L_sem (Phase 1+2)

Evidence から L_P, L_Q, L_X, L_R, L_A を算出する薄いラッパー。
Phase 3 で L_G (因果構造) を追加予定。

参照: docs/semantic_loss.md
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


# --- デフォルト重み (Phase 4 で HA48+ から校正予定) ---
DEFAULT_WEIGHTS: Dict[str, float] = {
    "L_P": 0.35,
    "L_Q": 0.10,
    "L_R": 0.15,
    "L_A": 0.05,
    "L_G": 0.00,
    "L_X": 0.35,
}


@dataclass(frozen=True)
class SemanticLoss:
    """意味損失関数の各項と合計"""

    L_P: Optional[float]          # 命題損失 [0,1]
    L_Q: float                    # 制約損失 [0,1]
    L_X: Optional[float]          # 極性反転損失 [0,1]
    L_R: Optional[float]          # 参照安定性損失 — Phase 2
    L_A: Optional[float]          # 曖昧性増大損失 — Phase 2
    L_G: Optional[float]          # 因果構造損失 — Phase 3
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
    weights: Optional[Dict[str, float]] = None,
) -> SemanticLoss:
    """Evidence から意味損失関数を算出する (Phase 1+2)

    Phase 1: L_P, L_Q, L_X
    Phase 2: L_R (f4 → 参照安定性), L_A (f1 → 曖昧性増大)
    Phase 3 追加予定: L_G (因果構造, grv 統合)

    Args:
        evidence: 検出層の出力
        propositions: 命題テキストのリスト (L_X 算出に必要)
        weights: 各項の重み (未指定時は DEFAULT_WEIGHTS)
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS

    L_P = _compute_L_P(evidence)
    L_Q = _compute_L_Q(evidence)
    L_X = _compute_L_X(evidence, propositions)
    L_R = _compute_L_R(evidence)
    L_A = _compute_L_A(evidence)

    # Phase 3 stub
    L_G: Optional[float] = None

    components = {
        "L_P": L_P, "L_Q": L_Q, "L_X": L_X,
        "L_R": L_R, "L_A": L_A, "L_G": L_G,
    }
    L_total, weights_used = _weighted_total(components, w)

    return SemanticLoss(
        L_P=L_P,
        L_Q=L_Q,
        L_X=L_X,
        L_R=L_R,
        L_A=L_A,
        L_G=L_G,
        L_total=L_total,
        weights_used=weights_used,
    )
