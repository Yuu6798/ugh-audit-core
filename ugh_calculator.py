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

# --- verdict 閾値 (HA48 検証済み確定値) ---
VERDICT_ACCEPT = 0.10
VERDICT_REWRITE = 0.25

# --- ΔE ビン閾値 (HA48 検証済み確定値に統一) ---
DELTA_E_BIN_THRESHOLDS = [VERDICT_ACCEPT, VERDICT_REWRITE]

# --- 有効な verdict / mode 値 ---
VALID_VERDICTS = frozenset({"accept", "rewrite", "regenerate", "degraded"})
VALID_MODES = frozenset({"computed", "computed_ai_draft", "degraded"})
VALID_METADATA_SOURCES = frozenset({"inline", "llm_generated", "none", "fallback"})
META_SOURCE_LLM = "llm_generated"
META_SOURCE_INLINE = "inline"
META_SOURCE_NONE = "none"
META_SOURCE_FALLBACK = "fallback"
GATE_FAIL = "fail"

# --- C ビン閾値 ---
C_BIN_THRESHOLDS = [0.34, 0.67]  # bin1/2境界, bin2/3境界

# --- hit_sources ソース値 (detector.py との契約) ---
HIT_SOURCE_TFIDF = "tfidf"              # core pipeline deterministic hit
HIT_SOURCE_CASCADE = "cascade_rescued"  # cascade layer probabilistic hit
HIT_SOURCE_MISS = "miss"                # no hit in either layer


def summarize_hit_sources(
    hit_sources: Dict[int, str],
    propositions_total: int,
) -> Optional[Dict[str, object]]:
    """Evidence.hit_sources を API 公開用の構造化サマリへ変換する。

    core / cascade / miss の件数と、core-only hit rate（論文の決定性主張の
    分子）を明示する。`per_proposition` は命題インデックス → ソース
    マッピングで、JSON シリアライズ用に key を str へ変換する。

    **内部整合性の保証:** `core_hit + cascade_rescued + miss == total` が
    常に成立する。`miss` は `total` から derive することで、hit_sources
    mapping が `propositions_total` 未満しか含まない場合（例: server の
    compat fallback で `{}` が渡される場合）でも miss rate を過小報告
    しない。

    propositions_total=0 (命題未検出) の場合は None を返す。
    """
    if propositions_total <= 0:
        return None

    # core / cascade は explicit label を数える（検出層が明示したヒットのみ）
    core_hit = sum(1 for v in hit_sources.values() if v == HIT_SOURCE_TFIDF)
    cascade_rescued = sum(
        1 for v in hit_sources.values() if v == HIT_SOURCE_CASCADE
    )
    # miss は total から derive する。これにより:
    #   (a) core + cascade + miss == total を常に保証
    #   (b) hit_sources に explicit "miss" label がなくても total 分だけ
    #       miss に計上される（= 不明命題を保守的に non-hit 扱い）
    # Codex review P2: 空 mapping + 非ゼロ total で miss=0 になる bug を修正。
    # max(0, ...) は hit_sources が total を超えて tfidf/cascade を持つ
    # 想定外入力に対する防御ガード。
    miss = max(0, propositions_total - core_hit - cascade_rescued)

    return {
        "core_hit": core_hit,
        "cascade_rescued": cascade_rescued,
        "miss": miss,
        "total": propositions_total,
        # core-only hit rate: 論文・査読で「決定性」を主張する際の分子。
        # cascade を含めない tfidf-only のヒット数 / 全命題数。
        "core_only_hit_rate": f"{core_hit}/{propositions_total}",
        # 命題 index → ソース。JSON 互換のため key を str にする。
        # compat fallback で mapping が total を下回る場合、ここも部分的。
        "per_proposition": {str(k): v for k, v in hit_sources.items()},
    }


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
    mode_affordance_primary: str = ""                          # response mode (primary)
    mode_affordance_secondary: List[str] = field(default_factory=list)  # response mode (secondary, 0-2)
    mode_affordance_closure: str = ""                          # "closed" | "qualified" | "open" | ""
    mode_affordance_action_required: Optional[bool] = None     # action needed in response
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
    grv: Optional[float] = None        # 因果構造損失 [0,1] / None=SBert未導入
    grv_tag: str = "none"             # "none" | "low_gravity" | "mid_gravity" | "high_gravity"


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
    """ΔEをビンに分類する (1-3, HA48 検証済み確定値)

    bin 1: ΔE ≤ 0.10  → accept
    bin 2: 0.10 < ΔE ≤ 0.25  → rewrite
    bin 3: ΔE > 0.25  → regenerate
    """
    if delta_e <= DELTA_E_BIN_THRESHOLDS[0]:
        return 1
    if delta_e <= DELTA_E_BIN_THRESHOLDS[1]:
        return 2
    return 3


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
            grv=None,
            grv_tag="none",
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
        grv=None,
        grv_tag="none",
    )


def derive_verdict(state: State) -> str:
    """State から verdict を導出する (HA48 検証済み確定値)

    ΔE ≤ 0.10  → accept
    0.10 < ΔE ≤ 0.25  → rewrite
    ΔE > 0.25  → regenerate
    C=None (ΔE算出不可) → degraded
    """
    if state.delta_e is None:
        return "degraded"
    if state.delta_e <= VERDICT_ACCEPT:
        return "accept"
    if state.delta_e <= VERDICT_REWRITE:
        return "rewrite"
    return "regenerate"


def derive_mode(state: State, *, metadata_source: str = "none") -> str:
    """State + metadata_source から mode を導出する

    C が存在しなければ degraded。
    C が存在し metadata_source が llm_generated なら computed_ai_draft。
    それ以外は computed。
    """
    if state.C is None:
        return "degraded"
    if metadata_source == META_SOURCE_LLM:
        return "computed_ai_draft"
    return "computed"
