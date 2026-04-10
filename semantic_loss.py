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
# fail-fast: detector モジュール自体が見つからない場合のみフォールバックし、
# detector の transitive 依存エラー (yaml 未インストール等) は再送出する。
# これにより L_X が沈黙して L_total が誤って良化することを防ぐ。
try:
    from detector import detect_operator, OPERATOR_CATALOG
    _HAS_DETECTOR = True
except ModuleNotFoundError as _err:
    if _err.name == "detector":
        _HAS_DETECTOR = False
    else:
        raise


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


# 否定 deontic の検出用トークン
# 「べきではない」等は detect_operator では deontic family (priority 1) に
# 解決されるが、極性反転の実質を持つため L_X に含める必要がある。
# detector.py の needs_polarity_full ロジックと整合。
_NEG_DEONTIC_TOKENS = ("べきではない", "すべきではない")


def _is_polarity_bearing(proposition: str) -> bool:
    """命題が polarity-bearing (極性反転の対象) かどうかを判定する

    対象:
    1. negation 族 (effect == "polarity_flip") の演算子を含む
    2. 否定 deontic ("べきではない" 等) を含む — detector の needs_polarity_full と同じ扱い
    """
    if not _HAS_DETECTOR:
        return False
    op = detect_operator(proposition)
    if op is not None and OPERATOR_CATALOG[op.family]["effect"] == "polarity_flip":
        return True
    return any(tok in proposition for tok in _NEG_DEONTIC_TOKENS)


def _compute_L_X(
    evidence: Evidence,
    propositions: Optional[List[str]],
) -> Optional[float]:
    """L_X = 極性反転損失 (polarity-bearing 命題の miss 率)

    L_X = |polarity-bearing ∩ miss| / |polarity-bearing|

    polarity-bearing 命題 = negation 族 (polarity_flip) または否定 deontic を含む命題。
    detector の needs_polarity_full ロジックと整合する。

    命題テキストが未提供、または polarity-bearing 命題が 1 件もない場合は None (degraded)。
    全体の命題数で割る旧実装と異なり、polarity 信号が薄められないよう
    polarity-bearing 部分集合のみで正規化する。
    """
    if evidence.propositions_total == 0 or not propositions:
        return None
    if not _HAS_DETECTOR:
        return None

    # polarity-bearing 命題を先に列挙
    polarity_indices = {
        idx for idx in range(min(len(propositions), evidence.propositions_total))
        if _is_polarity_bearing(propositions[idx])
    }
    if not polarity_indices:
        # 極性制約が存在しない → degraded (undefined)
        return None

    miss_set = set(evidence.miss_ids)
    polarity_misses = len(polarity_indices & miss_set)
    return _clamp(polarity_misses / len(polarity_indices))


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
