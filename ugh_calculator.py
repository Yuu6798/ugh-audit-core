"""ugh_calculator.py — 電卓層

Evidence → State の決定的変換を行う。
推論ゼロ: 同じ入力なら同じ出力。embedding/LLM呼び出しなし。

計算式:
    S = 1 - Σ(w_k × f_k) / Σ(w_k)       # 構造完全性 [0,1]
    C = hits / n_propositions              # 命題被覆率 [0,1]
    ΔE = (w_s(1-S)² + w_c(1-C)²) / (w_s + w_c)  # 距離 [0,1]
    quality_score = 5 - 4 * ΔE            # 品質スコア [1,5]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# --- 重み定数 ---
WEIGHTS_F = {"f1": 5, "f2": 25, "f3": 5, "f4": 5}  # 構造要素の重み
WEIGHT_S = 2   # ΔE計算における S の重み
WEIGHT_C = 1   # ΔE計算における C の重み

# --- ΔE ビン閾値 ---
DELTA_E_BIN_THRESHOLDS = [0.02, 0.12, 0.35]  # bin1/2境界, bin2/3境界, bin3/4境界

# --- C ビン閾値 ---
C_BIN_THRESHOLDS = [0.34, 0.67]  # bin1/2境界, bin2/3境界


@dataclass(frozen=True)
class Evidence:
    """検出層の出力: テキストから抽出した事実のみ（判断なし）"""

    question_id: str
    f1_anchor: float = 0.0                # 主題逸脱 (0.0 / 0.5 / 1.0)
    f2_unknown: float = 0.0               # 用語捏造 (0.0 / 0.5 / 1.0)
    f3_operator: float = 0.0              # 演算子無処理 (0.0 / 0.5 / 1.0)
    f4_premise: Optional[float] = 0.0     # 前提受容 (0.0 / 0.5 / 1.0 / None=未計算)
    f2_detail: str = ""          # f2検出詳細
    f4_detail: str = ""          # f4検出詳細
    f3_operator_family: str = "" # 検出された演算子族
    f4_trap_type: str = ""       # 検出されたtrap_type
    propositions_hit: int = 0    # ヒット命題数
    propositions_total: int = 0  # 全命題数
    hit_ids: List[int] = field(default_factory=list)   # ヒット命題インデックス
    miss_ids: List[int] = field(default_factory=list)  # ミス命題インデックス
    hit_sources: Dict[int, str] = field(default_factory=dict)  # 命題idx → "tfidf"/"cascade_rescued"/"miss"


@dataclass(frozen=True)
class State:
    """電卓層の出力: 数値のみ（解釈なし）"""

    S: float                          # 構造完全性 [0,1]
    C: Optional[float]                # 命題被覆率 [0,1] / None=未計算
    delta_e: Optional[float]          # 意味距離 [0,1] / None=未計算
    quality_score: Optional[float]    # 品質スコア [1,5] / None=未計算
    delta_e_bin: Optional[int]        # ΔEビン (1-4) / None=未計算
    C_bin: Optional[int]              # Cビン (1-3) / None=未計算
    por_state: str                    # PoR状態 ("inactive" — 本エンジンではPoR非使用)
    grv_tag: str                      # "none" | "low" | "moderate" | "high"


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """値を [lo, hi] にクランプする"""
    return max(lo, min(hi, value))


def _compute_s(evidence: Evidence) -> float:
    """構造完全性 S を計算する

    S = 1 - Σ(w_k × f_k) / Σ(w_k)
    w = {f1:5, f2:25, f3:5, f4:5}

    f4=None の場合、f4 の重み（5）を分母から除外する:
        S = 1 - (5×f1 + 25×f2 + 5×f3) / 35
    """
    weighted_sum = (
        WEIGHTS_F["f1"] * evidence.f1_anchor
        + WEIGHTS_F["f2"] * evidence.f2_unknown
        + WEIGHTS_F["f3"] * evidence.f3_operator
    )
    total_weight = WEIGHTS_F["f1"] + WEIGHTS_F["f2"] + WEIGHTS_F["f3"]

    if evidence.f4_premise is not None:
        weighted_sum += WEIGHTS_F["f4"] * evidence.f4_premise
        total_weight += WEIGHTS_F["f4"]

    return _clamp(1.0 - weighted_sum / total_weight)


def _compute_c(evidence: Evidence) -> Optional[float]:
    """命題被覆率 C を計算する

    C = hits / n_propositions
    命題数が0の場合は None を返す（命題未提供 = 計算不能）
    """
    if evidence.propositions_total == 0:
        return None
    return _clamp(evidence.propositions_hit / evidence.propositions_total)


def _compute_delta_e(s: float, c: float) -> float:
    """ΔE（意味距離）を計算する

    ΔE = (w_s(1-S)² + w_c(1-C)²) / (w_s + w_c)
    w_s=2, w_c=1
    """
    numerator = WEIGHT_S * (1.0 - s) ** 2 + WEIGHT_C * (1.0 - c) ** 2
    denominator = WEIGHT_S + WEIGHT_C
    return _clamp(numerator / denominator)


def _bin_delta_e(delta_e: float) -> int:
    """ΔEをビンに分類する (1-4)

    bin 1: ΔE ≤ 0.02
    bin 2: 0.02 < ΔE ≤ 0.12
    bin 3: 0.12 < ΔE ≤ 0.35
    bin 4: ΔE > 0.35
    """
    if delta_e <= DELTA_E_BIN_THRESHOLDS[0]:
        return 1
    if delta_e <= DELTA_E_BIN_THRESHOLDS[1]:
        return 2
    if delta_e <= DELTA_E_BIN_THRESHOLDS[2]:
        return 3
    return 4


def _bin_c(c: float) -> int:
    """Cをビンに分類する (1-3)

    bin 1: C < 0.34
    bin 2: 0.34 ≤ C < 0.67
    bin 3: C ≥ 0.67
    """
    if c < C_BIN_THRESHOLDS[0]:
        return 1
    if c < C_BIN_THRESHOLDS[1]:
        return 2
    return 3


def _grv_tag(evidence: Evidence) -> str:
    """grv_tagを判定する

    本エンジンではgrv計算は後回し。常に "none" を返す。
    """
    return "none"


def _compute_quality_score(delta_e: float) -> float:
    """品質スコアを計算する

    quality_score = 5 - 4 * ΔE
    パラメータフリー。ΔE=0 → 5.0, ΔE=1 → 1.0
    """
    return max(1.0, min(5.0, 5.0 - 4.0 * delta_e))


def calculate(evidence: Evidence) -> State:
    """電卓層: Evidence → State

    全計算が決定的。同じ入力なら同じ出力。
    C=None の場合、ΔE / quality_score / ビンは算出しない（None を返す）。
    """
    s = _compute_s(evidence)
    c = _compute_c(evidence)

    if c is not None:
        delta_e = _compute_delta_e(s, c)
        quality_score = _compute_quality_score(delta_e)
        delta_e_bin = _bin_delta_e(delta_e)
        c_bin = _bin_c(c)
        return State(
            S=round(s, 4),
            C=round(c, 4),
            delta_e=round(delta_e, 4),
            quality_score=round(quality_score, 4),
            delta_e_bin=delta_e_bin,
            C_bin=c_bin,
            por_state="inactive",
            grv_tag=_grv_tag(evidence),
        )

    # C=None: 命題未提供のため ΔE 算出不可
    return State(
        S=round(s, 4),
        C=None,
        delta_e=None,
        quality_score=None,
        delta_e_bin=None,
        C_bin=None,
        por_state="inactive",
        grv_tag=_grv_tag(evidence),
    )
