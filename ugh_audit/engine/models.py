from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


DecisionLabel = Literal[
    "same_meaning",
    "minor_drift",
    "meaning_drift",
    "unknown",
]


@dataclass(frozen=True)
class Evidence:
    """検出層の事実出力（まだ解釈しない）"""

    question: str
    response: str
    reference: Optional[str] = None
    reference_core: Optional[str] = None
    n_propositions: int = 0
    proposition_hits: int = 0
    f1_anchor: float = 0.0
    f2_operator: float = 0.0
    f3_reason_request: float = 0.0
    f4_forbidden_reinterpret: float = 0.0
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class State:
    """電卓層の数値出力"""

    s: float
    c: float
    delta_e: float
    grv: float
    delta_e_bin: str
    c_bin: str
    por_state: str
    grv_tag: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    """判定層の出力"""

    decision: DecisionLabel
    verdict_label: str
    repair_order: List[str] = field(default_factory=list)
    rationale: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Budget:
    """修復コスト見積り"""

    cost: int = 0
    opcodes: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineResult:
    """新エンジンの canonical 出力"""

    evidence: Evidence
    state: State
    policy: Policy
    budget: Budget


@dataclass(frozen=True)
class EngineConfig:
    """README 記載の初期重み・bin 設定の最小雛形"""

    weight_f1: float = 5.0
    weight_f2: float = 25.0
    weight_f3: float = 5.0
    weight_f4: float = 5.0
    weight_s: float = 2.0
    weight_c: float = 1.0
    beta: float = 0.5
    same_meaning_max: float = 0.04
    minor_drift_max: float = 0.10
    low_coverage_max: float = 0.33
    medium_coverage_max: float = 0.66
