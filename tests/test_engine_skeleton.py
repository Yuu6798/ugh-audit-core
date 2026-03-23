from ugh_audit.engine import (
    EngineConfig,
    Evidence,
    build_budget,
    build_policy,
    build_state,
    compute_c,
    compute_delta_e,
    compute_s,
)


def test_compute_s_and_c():
    e = Evidence(
        question="Q",
        response="R",
        n_propositions=4,
        proposition_hits=3,
        f1_anchor=0.0,
        f2_operator=1.0,
        f3_reason_request=0.0,
        f4_forbidden_reinterpret=0.0,
    )
    s = compute_s(e)
    c = compute_c(e)
    assert 0.0 <= s <= 1.0
    assert c == 0.75


def test_delta_e_is_deterministic():
    value = compute_delta_e(0.5, 0.5, EngineConfig())
    assert round(value, 6) == round((2 * (0.5**2) + 1 * (0.5**2)) / 3, 6)


def test_build_state_policy_budget():
    e = Evidence(
        question="Q",
        response="R",
        n_propositions=2,
        proposition_hits=0,
        f1_anchor=1.0,
        f2_operator=1.0,
        f3_reason_request=0.0,
        f4_forbidden_reinterpret=1.0,
    )
    state = build_state(e, entropy_ratio=0.2, centroid_cosine=0.1)
    policy = build_policy(state)
    budget = build_budget(policy)

    assert state.delta_e_bin in {"same_meaning", "minor_drift", "meaning_drift"}
    assert policy.verdict_label in {"同一意味圏", "軽微なズレ", "意味乖離"}
    assert budget.cost >= 0
