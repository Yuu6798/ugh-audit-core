"""mode_grv.py — mode_conditioned_grv v2: モード条件付き grv 解釈ベクトル

grv_raw と mode_affordance を組み合わせ、モード固有の 4 成分解釈ベクトルを生成する。
grv_raw を置き換えない。説明用ベクトルとして併走する。

4 成分:
  anchor_alignment: 高重力語が問いの核語と mode に必要な語彙へ乗っているか [0,1] (高=良)
  balance:          比較型・探索型で重力が片側に潰れていないか [0,1] (高=良)
  boilerplate_risk: 安全一般論・回避表現に重力が吸われていないか [0,1] (高=危)
  collapse_risk:    複数論点が要るのに 1 塊に潰れていないか [0,1] (高=危)

設計: docs/mode_affordance_v1_addendum.md §4 の将来構想を操作化。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from grv_calculator import GrvResult

# --- モード別の重要成分マッピング ---
# addendum §4: comparative→balance, critical→anchor+boilerplate,
# exploratory→collapse, action_required→anchor+boilerplate
_MODE_FOCUS: Dict[str, List[str]] = {
    "definitional":  ["anchor_alignment"],
    "analytical":    ["anchor_alignment"],
    "evaluative":    ["anchor_alignment", "boilerplate_risk"],
    "comparative":   ["balance", "anchor_alignment"],
    "critical":      ["anchor_alignment", "boilerplate_risk"],
    "exploratory":   ["collapse_risk", "balance"],
}

# --- Boilerplate 検出パターン (決定的、SBert 不要) ---
_BOILERPLATE_PATTERNS = re.compile(
    r"(?:"
    r"倫理的な?(?:観点|配慮|問題)|"
    r"安全性[をにの]|安全上の|安全(?:で[あす]|を確保)|"
    r"責任[あをが]|責任(?:の所在|を持)|"
    r"慎重[にな]|慎重(?:に検討|な対応)|"
    r"一般的に[はの]?|一般論として|"
    r"多面的に|さまざまな|様々な(?:観点|視点|側面)|"
    r"社会的(?:な|に)(?:影響|責任|意義)|"
    r"バランス(?:が|を|の)(?:取|重要)|"
    r"総合的に(?:判断|考慮|検討)|"
    r"適切(?:な|に)(?:対応|運用|管理|配慮)|"
    r"リスク(?:を|の)(?:考慮|管理|評価)|"
    r"透明性[をのが]"
    r")"
)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _split_sentences(text: str) -> List[str]:
    """テキストを文単位に分割する (grv_calculator と同じロジック)"""
    parts = re.split(r'[。．！？!?.\n]+', text)
    return [p.strip() for p in parts if p.strip()]


# --- 4 成分の計算 ---


def _compute_anchor_alignment(grv: GrvResult) -> float:
    """anchor_alignment: 回答が問いの核 + 命題にどれだけ到達しているか

    cover_soft (命題→応答の連続到達度) をベースにし、
    drift (重心逸脱) で補正する。
    高い値 = 良好な到達。
    """
    # cover_soft は [0,1] で高い = 命題によく到達
    # drift は [0,1] で高い = 問いから逸脱
    # 合成: cover_soft を主、drift を減点として使う
    if grv.n_propositions == 0:
        # 命題なし → drift の反転のみで近似
        return _clamp(1.0 - grv.drift)

    return _clamp(grv.cover_soft * 0.7 + (1.0 - grv.drift) * 0.3)


def _compute_balance(grv: GrvResult) -> Optional[float]:
    """balance: 命題カバレッジが均等かどうか

    comparative / exploratory で重要。
    cover_soft_per_proposition の分散が低い = 均等 = 良好。
    命題が 2 件未満の場合は None (balance 判定不能)。
    """
    per_prop = grv.cover_soft_per_proposition
    if len(per_prop) < 2:
        return None

    std = statistics.pstdev(per_prop)
    # pstdev (母集団標準偏差) を使用。命題群は標本ではなく全数。
    # 理論最大値は 0.5 (0 と 1 が半々)。
    # std=0 → balance=1.0 (完全均等)
    # std≥0.3 → balance≈0.0
    return _clamp(1.0 - std / 0.3)


def _compute_boilerplate_risk(response_text: str) -> float:
    """boilerplate_risk: 回答中のボイラープレート密度

    安全一般論・倫理一般論・回避表現の文の割合。
    決定的 (regex ベース)、SBert 不要。
    """
    sentences = _split_sentences(response_text)
    if not sentences:
        return 0.0

    bp_count = sum(1 for s in sentences if _BOILERPLATE_PATTERNS.search(s))
    return _clamp(bp_count / len(sentences))


def _compute_collapse_risk(grv: GrvResult) -> Optional[float]:
    """collapse_risk: 複数論点が 1 塊に潰れていないか

    collapse_v2 (残留型: 命題で説明できない残留) をベースに、
    命題数が多いほどリスクを増幅する。
    命題が 2 件未満の場合は None (collapse 判定不能)。
    """
    if grv.n_propositions < 2 or not grv.collapse_v2_applicable:
        return None

    # collapse_v2 は [0,1] で高い = 命題で説明できない残留が多い
    # 命題数が多いとき、collapse が高いのはより深刻
    n_factor = min(grv.n_propositions / 3.0, 1.0)  # 3命題以上で 1.0
    return _clamp(grv.collapse_v2 * (0.7 + 0.3 * n_factor))


# --- Result dataclass ---


@dataclass(frozen=True)
class ModeConditionedGrv:
    """mode_conditioned_grv v2 解釈ベクトル"""

    anchor_alignment: float                         # [0,1] 高=良好
    balance: Optional[float]                        # [0,1] 高=均等 (comparative/exploratory)
    boilerplate_risk: float                         # [0,1] 高=危険
    collapse_risk: Optional[float]                  # [0,1] 高=危険
    mode: str                                       # primary mode
    focus_components: List[str] = field(default_factory=list)  # このモードで重要な成分
    grv_raw: float = 0.0                            # 元の grv 値 (参照用)
    version: str = "v2.0"


# --- Public API ---


def compute_mode_conditioned_grv(
    *,
    grv_result: GrvResult,
    response_text: str,
    mode_affordance_primary: str,
    action_required: bool = False,
) -> Optional[ModeConditionedGrv]:
    """mode_conditioned_grv を計算する。

    Args:
        grv_result: compute_grv() の出力
        response_text: AI の回答テキスト
        mode_affordance_primary: 質問の primary mode (6-mode enum)
        action_required: mode_affordance.action_required

    Returns:
        ModeConditionedGrv、または mode が不明の場合は None
    """
    if mode_affordance_primary not in _MODE_FOCUS:
        return None

    anchor = _compute_anchor_alignment(grv_result)
    balance = _compute_balance(grv_result)
    boilerplate = _compute_boilerplate_risk(response_text)
    collapse = _compute_collapse_risk(grv_result)

    focus = list(_MODE_FOCUS[mode_affordance_primary])
    if action_required and "boilerplate_risk" not in focus:
        focus.append("boilerplate_risk")

    return ModeConditionedGrv(
        anchor_alignment=round(anchor, 4),
        balance=round(balance, 4) if balance is not None else None,
        boilerplate_risk=round(boilerplate, 4),
        collapse_risk=round(collapse, 4) if collapse is not None else None,
        mode=mode_affordance_primary,
        focus_components=focus,
        grv_raw=grv_result.grv,
    )
