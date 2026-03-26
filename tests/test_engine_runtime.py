from ugh_audit.engine import Evidence, UGHAuditEngine, to_legacy_payload


def test_engine_runtime_returns_canonical_result():
    engine = UGHAuditEngine()
    evidence = Evidence(
        question="PoRが高ければ誠実か？",
        response="高PoRでも誠実性は保証されない。",
        reference="PoRは十分条件ではない。",
        n_propositions=2,
        proposition_hits=1,
        f1_anchor=0.0,
        f2_operator=0.0,
        f3_reason_request=0.0,
        f4_forbidden_reinterpret=0.0,
    )

    result = engine.run(evidence, entropy_ratio=0.7, centroid_cosine=0.8)

    assert result.evidence.question == "PoRが高ければ誠実か？"
    assert 0.0 <= result.state.s <= 1.0
    assert 0.0 <= result.state.c <= 1.0
    assert result.policy.verdict_label in {"同一意味圏", "軽微なズレ", "意味乖離"}
    assert result.budget.cost >= 0


def test_engine_from_inputs_builds_evidence_and_legacy_payload():
    engine = UGHAuditEngine()

    result = engine.from_inputs(
        question="AIは常にバイアスを含むか？",
        response="常に、ではなく文脈依存。",
        n_propositions=3,
        proposition_hits=2,
        f2_operator=1.0,
        notes=["operator_universal"],
        entropy_ratio=0.4,
        centroid_cosine=0.6,
        extra={"question_id": "q095"},
    )
    payload = to_legacy_payload(result)

    assert payload["por"] == result.state.s
    assert payload["por_tuple"]["c"] == result.state.c
    assert payload["delta_e"] == result.state.delta_e
    assert payload["verdict"] == result.policy.verdict_label
    assert payload["engine_output"]["evidence"]["extra"]["question_id"] == "q095"
