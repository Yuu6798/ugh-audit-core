from __future__ import annotations

from typing import Optional

from .models import Budget, EngineConfig, Policy, State


def build_policy(state: State, config: Optional[EngineConfig] = None) -> Policy:
    _ = config or EngineConfig()

    if state.delta_e_bin == "same_meaning":
        decision = "same_meaning"
        verdict_label = "同一意味圏"
        repair_order = []
        rationale = ["delta_e_bin=same_meaning"]
    elif state.delta_e_bin == "minor_drift":
        decision = "minor_drift"
        verdict_label = "軽微なズレ"
        repair_order = ["tighten_proposition_coverage"]
        rationale = ["delta_e_bin=minor_drift"]
    else:
        decision = "meaning_drift"
        verdict_label = "意味乖離"
        repair_order = ["repair_core_proposition", "repair_forbidden_reinterpret"]
        rationale = ["delta_e_bin=meaning_drift"]

    return Policy(
        decision=decision,
        verdict_label=verdict_label,
        repair_order=repair_order,
        rationale=rationale,
        extra={"c_bin": state.c_bin, "por_state": state.por_state, "grv_tag": state.grv_tag},
    )


def build_budget(policy: Policy) -> Budget:
    opcode_cost = {
        "tighten_proposition_coverage": 1,
        "repair_core_proposition": 2,
        "repair_forbidden_reinterpret": 2,
    }
    opcodes = list(policy.repair_order)
    cost = sum(opcode_cost.get(op, 1) for op in opcodes)
    return Budget(cost=cost, opcodes=opcodes, extra={})
