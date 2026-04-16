from __future__ import annotations

from typing import Optional

from .models import EngineConfig, Evidence, State


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_s(evidence: Evidence, config: Optional[EngineConfig] = None) -> float:
    cfg = config or EngineConfig()
    numerator = (
        cfg.weight_f1 * evidence.f1_anchor
        + cfg.weight_f2 * evidence.f2_operator
        + cfg.weight_f3 * evidence.f3_reason_request
        + cfg.weight_f4 * evidence.f4_forbidden_reinterpret
    )
    denominator = cfg.weight_f1 + cfg.weight_f2 + cfg.weight_f3 + cfg.weight_f4
    return clamp(1.0 - (numerator / denominator if denominator else 0.0))


def compute_c(evidence: Evidence) -> float:
    if evidence.n_propositions <= 0:
        return 1.0  # 命題なし = 完全被覆と見なす（ugh_calculator.py と同一仕様）
    return clamp(evidence.proposition_hits / evidence.n_propositions)


def compute_delta_e(s: float, c: float, config: Optional[EngineConfig] = None) -> float:
    cfg = config or EngineConfig()
    numerator = cfg.weight_s * ((1.0 - s) ** 2) + cfg.weight_c * ((1.0 - c) ** 2)
    denominator = cfg.weight_s + cfg.weight_c
    return clamp(numerator / denominator if denominator else 0.0)


def compute_grv(*, entropy_ratio: float = 1.0, centroid_cosine: float = 1.0, config: Optional[EngineConfig] = None) -> float:
    cfg = config or EngineConfig()
    return clamp(cfg.beta * (1.0 - entropy_ratio) + (1.0 - cfg.beta) * (1.0 - centroid_cosine))


def bin_delta_e(delta_e: float, config: Optional[EngineConfig] = None) -> str:
    cfg = config or EngineConfig()
    if delta_e <= cfg.same_meaning_max:
        return "same_meaning"
    if delta_e <= cfg.minor_drift_max:
        return "minor_drift"
    return "meaning_drift"


def bin_c(c: float, config: Optional[EngineConfig] = None) -> str:
    cfg = config or EngineConfig()
    if c <= cfg.low_coverage_max:
        return "low"
    if c <= cfg.medium_coverage_max:
        return "medium"
    return "high"


def por_state(s: float, c: float) -> str:
    if s >= 0.8 and c >= 0.8:
        return "stable"
    if s >= 0.5 and c >= 0.5:
        return "partial"
    return "unstable"


def grv_tag(grv: float) -> str:
    if grv >= 0.66:
        return "high_gravity"
    if grv >= 0.33:
        return "mid_gravity"
    return "low_gravity"


def build_state(
    evidence: Evidence,
    *,
    entropy_ratio: float = 1.0,
    centroid_cosine: float = 1.0,
    config: Optional[EngineConfig] = None,
) -> State:
    cfg = config or EngineConfig()
    s = compute_s(evidence, cfg)
    c = compute_c(evidence)
    delta_e = compute_delta_e(s, c, cfg)
    grv = compute_grv(entropy_ratio=entropy_ratio, centroid_cosine=centroid_cosine, config=cfg)
    return State(
        s=s,
        c=c,
        delta_e=delta_e,
        grv=grv,
        delta_e_bin=bin_delta_e(delta_e, cfg),
        c_bin=bin_c(c, cfg),
        por_state=por_state(s, c),
        grv_tag=grv_tag(grv),
        extra={},
    )
