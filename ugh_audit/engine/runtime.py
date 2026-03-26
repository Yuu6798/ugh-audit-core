from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .calculator import build_state
from .decision import build_budget, build_policy
from .models import EngineConfig, EngineResult, Evidence


class UGHAuditEngine:
    """新 engine の最小実行ファサード。

    detector 実装が揃う前でも、構造化 evidence から canonical output を
    一発で生成できる入口を提供する。
    """

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or EngineConfig()

    def run(
        self,
        evidence: Evidence,
        *,
        entropy_ratio: float = 1.0,
        centroid_cosine: float = 1.0,
    ) -> EngineResult:
        state = build_state(
            evidence,
            entropy_ratio=entropy_ratio,
            centroid_cosine=centroid_cosine,
            config=self.config,
        )
        policy = build_policy(state, self.config)
        budget = build_budget(policy)
        return EngineResult(evidence=evidence, state=state, policy=policy, budget=budget)

    def from_inputs(
        self,
        *,
        question: str,
        response: str,
        reference: Optional[str] = None,
        reference_core: Optional[str] = None,
        n_propositions: int = 0,
        proposition_hits: int = 0,
        f1_anchor: float = 0.0,
        f2_operator: float = 0.0,
        f3_reason_request: float = 0.0,
        f4_forbidden_reinterpret: float = 0.0,
        notes: Optional[Sequence[str]] = None,
        entropy_ratio: float = 1.0,
        centroid_cosine: float = 1.0,
        extra: Optional[dict] = None,
    ) -> EngineResult:
        evidence = Evidence(
            question=question,
            response=response,
            reference=reference,
            reference_core=reference_core,
            n_propositions=n_propositions,
            proposition_hits=proposition_hits,
            f1_anchor=f1_anchor,
            f2_operator=f2_operator,
            f3_reason_request=f3_reason_request,
            f4_forbidden_reinterpret=f4_forbidden_reinterpret,
            notes=list(notes or []),
            extra=dict(extra or {}),
        )
        return self.run(
            evidence,
            entropy_ratio=entropy_ratio,
            centroid_cosine=centroid_cosine,
        )


def to_legacy_payload(result: EngineResult) -> dict:
    """旧 API / DB が当面消費できる互換 payload へ射影する。"""

    return {
        "por": result.state.s,
        "por_tuple": {"s": result.state.s, "c": result.state.c},
        "delta_e": result.state.delta_e,
        "grv": result.state.grv,
        "verdict": result.policy.verdict_label,
        "decision": result.policy.decision,
        "repair_order": list(result.policy.repair_order),
        "budget_cost": result.budget.cost,
        "budget_opcodes": list(result.budget.opcodes),
        "engine_output": {
            "evidence": {
                "question": result.evidence.question,
                "response": result.evidence.response,
                "reference": result.evidence.reference,
                "reference_core": result.evidence.reference_core,
                "n_propositions": result.evidence.n_propositions,
                "proposition_hits": result.evidence.proposition_hits,
                "f1_anchor": result.evidence.f1_anchor,
                "f2_operator": result.evidence.f2_operator,
                "f3_reason_request": result.evidence.f3_reason_request,
                "f4_forbidden_reinterpret": result.evidence.f4_forbidden_reinterpret,
                "notes": list(result.evidence.notes),
                "extra": dict(result.evidence.extra),
            },
            "state": {
                "s": result.state.s,
                "c": result.state.c,
                "delta_e": result.state.delta_e,
                "grv": result.state.grv,
                "delta_e_bin": result.state.delta_e_bin,
                "c_bin": result.state.c_bin,
                "por_state": result.state.por_state,
                "grv_tag": result.state.grv_tag,
                "extra": dict(result.state.extra),
            },
            "policy": {
                "decision": result.policy.decision,
                "verdict_label": result.policy.verdict_label,
                "repair_order": list(result.policy.repair_order),
                "rationale": list(result.policy.rationale),
                "extra": dict(result.policy.extra),
            },
            "budget": {
                "cost": result.budget.cost,
                "opcodes": list(result.budget.opcodes),
                "extra": dict(result.budget.extra),
            },
        },
    }
