"""
mode_signal.py
Deterministic response-mode compliance scorer for mode_affordance v1.

Computes response_mode_signal — a non-binding signal that measures how well
a response matches the expected response form (mode_affordance) of a question.
Follows the grv_calculator.py pattern: computed after verdict, never affects
S / C / delta_e / quality_score / verdict.

No LLM, no embeddings, no external APIs. All detection is regex/cue-list based.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MODE_SIGNAL_VERSION = "v1.0"

# ---------------------------------------------------------------------------
# Valid enums
# ---------------------------------------------------------------------------

VALID_MODES_6 = frozenset({
    "definitional", "analytical", "evaluative",
    "comparative", "critical", "exploratory",
})

VALID_CLOSURE = frozenset({"closed", "qualified", "open"})

# ---------------------------------------------------------------------------
# Required moves per mode (exactly 2 moves each)
# ---------------------------------------------------------------------------

REQUIRED_MOVES: Dict[str, tuple] = {
    "definitional": ("define_target", "set_boundary"),
    "analytical": ("show_structure_or_causality", "identify_mechanism_or_condition"),
    "evaluative": ("state_criteria", "give_judgment"),
    "comparative": ("name_both_targets", "compare_on_shared_axis"),
    "critical": ("inspect_premise", "reframe_if_needed"),
    "exploratory": ("map_options", "keep_open_if_needed"),
}

# ---------------------------------------------------------------------------
# Move cue patterns (Japanese text, compiled regex)
# ---------------------------------------------------------------------------

_MOVE_CUE_RAW: Dict[str, str] = {
    # definitional
    "define_target": (
        r"(?:とは|を指す|を意味する|のことを指す|のこと(?:で[あす])|"
        r"と定義|概念|定義(?:され|する|として)|"
        r"という(?:概念|用語|言葉|枠組み|考え方))"
    ),
    "set_boundary": (
        r"(?:に限(?:り|定|る)|範囲|文脈(?:で[はに])|"
        r"スコープ|境界|対象(?:と[しす]|は)|"
        r"という前提|を対象|には含(?:まない|めない)|"
        r"ではなく|とは(?:異な|別|区別))"
    ),
    # analytical
    "show_structure_or_causality": (
        r"(?:原因|要因|理由|なぜなら|ため(?:に|で[あす])|"
        r"から(?:で[あす]|こそ)|によって|に起因|"
        r"構造|因果|関係(?:性|する)|背景(?:にある|として))"
    ),
    "identify_mechanism_or_condition": (
        r"(?:メカニズム|仕組み|プロセス|過程|機構|"
        r"働き|機能(?:する|として)|作用|"
        r"条件|前提条件|の下で|場合(?:に[はのが]))"
    ),
    # evaluative
    "state_criteria": (
        r"(?:基準|観点|指標|尺度|評価軸|判断基準|"
        r"という点で|という意味で|に照らして|"
        r"の観点(?:から|で)|に基づ[きく])"
    ),
    "give_judgment": (
        r"(?:有効|妥当|適切|不適切|問題(?:がある|である)|"
        r"優れ|劣|評価(?:する|すると|できる)|"
        r"判断(?:する|できる|される)|値する|"
        r"十分|不十分|限界がある)"
    ),
    # comparative
    "name_both_targets": (
        r"(?:一方(?:で[はの])?|他方(?:で[はの])?|"
        r"前者|後者|に対して|"
        r"[AaBb]は.{0,20}[BbAa]は|"
        r"両者|双方|それぞれ)"
    ),
    "compare_on_shared_axis": (
        r"(?:比較|違い|異な[るり]|差異|対照|"
        r"共通(?:点|する|して)|同様|"
        r"(?:大きな|根本的な|本質的な)違い|"
        r"点(?:で[は異違共])|[にが]異なる)"
    ),
    # critical
    "inspect_premise": (
        r"(?:前提(?:と[しす]|を|に|が|は)|"
        r"暗黙(?:の|に|の前提)|想定(?:して|する|される)|"
        r"仮定(?:して|する|が|は)|"
        r"そもそも|実[はに]は|問題設定)"
    ),
    "reframe_if_needed": (
        r"(?:問い直[しす]|見直[しす]|再検討|再考|"
        r"本当に|必ずしも|とは限らない|"
        r"問題提起|別の見方|捉え直[しす]|"
        r"むしろ|ではなく)"
    ),
    # exploratory
    "map_options": (
        r"(?:可能性|選択肢|方向性|シナリオ|"
        r"考えられる|あり得る|複数の|"
        r"いくつか(?:の|ある)|パターン|"
        r"アプローチ|方法(?:として|が))"
    ),
    "keep_open_if_needed": (
        r"(?:仮に|もし|と仮定すると|という場合|"
        r"たとえば|ケースでは|想定すると|"
        r"一概に(?:は|言えない)|断定(?:できない|は難しい)|"
        r"未解決|今後の|さらなる(?:検討|研究|議論))"
    ),
}

# Compiled patterns (module-level, computed once)
MOVE_PATTERNS: Dict[str, re.Pattern] = {
    name: re.compile(pattern) for name, pattern in _MOVE_CUE_RAW.items()
}

# ---------------------------------------------------------------------------
# Closure cue patterns
# ---------------------------------------------------------------------------

_CLOSURE_CUE_RAW: Dict[str, str] = {
    "closed": (
        r"(?:したがって|結論として|以上から|まとめると|つまり|"
        r"以上により|よって|言える|である(?:。|$)|断言できる)"
    ),
    "qualified": (
        r"(?:ただし|一方で|留保|条件付き|限界|"
        r"ただ[、,]|しかし|ものの|必ずしも|"
        r"場合(?:によ|による)|例外|注意(?:が必要|すべき))"
    ),
    "open": (
        r"(?:今後の課題|検討が必要|明確ではない|さらなる|"
        r"未解決|今後|探求の余地|議論(?:が必要|の余地)|"
        r"一概に(?:は|言えない)|断定(?:できない|は難しい))"
    ),
}

CLOSURE_PATTERNS: Dict[str, re.Pattern] = {
    name: re.compile(pattern) for name, pattern in _CLOSURE_CUE_RAW.items()
}

# ---------------------------------------------------------------------------
# Action cue patterns
# ---------------------------------------------------------------------------

_ACTION_STRONG_RAW = (
    r"(?:すべき|必要がある|推奨(?:する|される)|"
    r"ステップ|手順(?:として|は)|対応(?:が求められる|すべき)|"
    r"導入(?:する|すべき)|実装(?:する|すべき)|設定(?:する|すべき))"
)

_ACTION_WEAK_RAW = (
    r"(?:望ましい|可能であれば|検討(?:してください|する(?:価値|こと))|"
    r"試みる|提案|考慮(?:する|すべき)|"
    r"取り組(?:む|み)|対策)"
)

ACTION_STRONG_PATTERN: re.Pattern = re.compile(_ACTION_STRONG_RAW)
ACTION_WEAK_PATTERN: re.Pattern = re.compile(_ACTION_WEAK_RAW)

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

WEIGHT_PRIMARY = 0.60
WEIGHT_SECONDARY = 0.20
WEIGHT_CLOSURE = 0.10
WEIGHT_ACTION = 0.10

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeSignalResult:
    """response_mode_signal output"""

    status: str                                   # "available" | "not_available"
    primary_mode: Optional[str] = None
    primary_score: Optional[float] = None
    secondary_scores: Dict[str, float] = field(default_factory=dict)
    closure_expected: Optional[str] = None
    closure_score: Optional[float] = None
    action_required: Optional[bool] = None
    action_score: Optional[float] = None
    overall_score: Optional[float] = None
    matched_moves: List[str] = field(default_factory=list)
    missing_moves: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------


def _score_moves(text: str, mode: str) -> tuple:
    """Score required moves for a mode. Returns (score, matched, missing)."""
    moves = REQUIRED_MOVES.get(mode)
    if not moves:
        return (0.0, [], list(moves) if moves else [])
    matched = []
    missing = []
    for move in moves:
        pattern = MOVE_PATTERNS.get(move)
        if pattern and pattern.search(text):
            matched.append(move)
        else:
            missing.append(move)
    score = len(matched) / len(moves) if moves else 0.0
    return (score, matched, missing)


def _score_closure(text: str, closure_type: str) -> Optional[float]:
    """Score closure compliance."""
    if not closure_type or closure_type not in VALID_CLOSURE:
        return None

    if closure_type == "closed":
        # Need a conclusion marker
        if CLOSURE_PATTERNS["closed"].search(text):
            return 1.0
        return 0.0

    if closure_type == "qualified":
        has_conclusion = bool(CLOSURE_PATTERNS["closed"].search(text))
        has_qualification = bool(CLOSURE_PATTERNS["qualified"].search(text))
        if has_conclusion and has_qualification:
            return 1.0
        if has_conclusion or has_qualification:
            return 0.5
        return 0.0

    if closure_type == "open":
        if CLOSURE_PATTERNS["open"].search(text):
            return 1.0
        return 0.0

    return None


def _score_action(text: str) -> float:
    """Score action presence in response."""
    if ACTION_STRONG_PATTERN.search(text):
        return 1.0
    if ACTION_WEAK_PATTERN.search(text):
        return 0.5
    return 0.0


def _weighted_overall(
    primary_score: float,
    secondary_scores: Dict[str, float],
    closure_score: Optional[float],
    action_score: Optional[float],
    action_required: Optional[bool],
) -> float:
    """Compute weighted overall score, normalizing for absent components."""
    total_weight = WEIGHT_PRIMARY
    weighted_sum = primary_score * WEIGHT_PRIMARY

    if secondary_scores:
        sec_avg = sum(secondary_scores.values()) / len(secondary_scores)
        weighted_sum += sec_avg * WEIGHT_SECONDARY
        total_weight += WEIGHT_SECONDARY

    if closure_score is not None:
        weighted_sum += closure_score * WEIGHT_CLOSURE
        total_weight += WEIGHT_CLOSURE

    if action_required and action_score is not None:
        weighted_sum += action_score * WEIGHT_ACTION
        total_weight += WEIGHT_ACTION

    if total_weight == 0:
        return 0.0
    return round(weighted_sum / total_weight, 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_NOT_AVAILABLE = ModeSignalResult(status="not_available")


def compute_mode_signal(
    *,
    response_text: str,
    mode_affordance_primary: str,
    mode_affordance_secondary: Optional[List[str]] = None,
    mode_affordance_closure: str = "",
    mode_affordance_action_required: Optional[bool] = None,
) -> ModeSignalResult:
    """Compute response_mode_signal for a given response.

    Returns ModeSignalResult with status="available" when mode_affordance_primary
    is a valid 6-mode value, otherwise status="not_available".

    Deterministic: same inputs → same outputs. No LLM/embedding calls.
    """
    if not mode_affordance_primary or mode_affordance_primary not in VALID_MODES_6:
        return _NOT_AVAILABLE

    secondary = mode_affordance_secondary or []

    # Score primary
    p_score, p_matched, p_missing = _score_moves(response_text, mode_affordance_primary)
    all_matched = list(p_matched)
    all_missing = list(p_missing)
    evidence_list: List[str] = []

    if p_matched:
        evidence_list.append(
            f"primary({mode_affordance_primary}): "
            f"matched {', '.join(p_matched)}"
        )
    if p_missing:
        evidence_list.append(
            f"primary({mode_affordance_primary}): "
            f"missing {', '.join(p_missing)}"
        )

    # Score secondaries
    sec_scores: Dict[str, float] = {}
    for sec_mode in secondary:
        if sec_mode in VALID_MODES_6 and sec_mode != mode_affordance_primary:
            s_score, s_matched, s_missing = _score_moves(response_text, sec_mode)
            sec_scores[sec_mode] = s_score
            all_matched.extend(s_matched)
            all_missing.extend(s_missing)
            if s_matched:
                evidence_list.append(
                    f"secondary({sec_mode}): matched {', '.join(s_matched)}"
                )

    # Score closure
    closure_score = _score_closure(response_text, mode_affordance_closure)
    if closure_score is not None:
        evidence_list.append(
            f"closure({mode_affordance_closure}): {closure_score}"
        )

    # Score action
    action_score: Optional[float] = None
    if mode_affordance_action_required:
        action_score = _score_action(response_text)
        evidence_list.append(f"action_required: {action_score}")

    # Overall
    overall = _weighted_overall(
        p_score, sec_scores, closure_score, action_score,
        mode_affordance_action_required,
    )

    # Deduplicate matched/missing (a move can appear in both primary and secondary)
    seen_matched = set()
    unique_matched = []
    for m in all_matched:
        if m not in seen_matched:
            unique_matched.append(m)
            seen_matched.add(m)

    seen_missing = set()
    unique_missing = []
    for m in all_missing:
        if m not in seen_missing and m not in seen_matched:
            unique_missing.append(m)
            seen_missing.add(m)

    return ModeSignalResult(
        status="available",
        primary_mode=mode_affordance_primary,
        primary_score=p_score,
        secondary_scores=sec_scores,
        closure_expected=mode_affordance_closure or None,
        closure_score=closure_score,
        action_required=mode_affordance_action_required,
        action_score=action_score,
        overall_score=overall,
        matched_moves=unique_matched,
        missing_moves=unique_missing,
        evidence=evidence_list,
    )
