"""Execution order and metadata boundaries of the required coverage stage."""
from ugh_audit import pipeline


def test_optional_diagnostics_cannot_consume_semantic_deadline(monkeypatch, semantic_complete_judge):
    clock = [100.0]
    events = []
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: clock[0])

    def judge(requirement, answer, **kwargs):
        events.append('semantic')
        return semantic_complete_judge(requirement, answer, **kwargs)

    def diagnostics(**kwargs):
        events.append('diagnostic')
        clock[0] += 40.0
        raise RuntimeError('optional calculation failure')

    monkeypatch.setattr(pipeline, '_run_legacy_diagnostics', diagnostics)
    result = pipeline.run_audit(question='Q', response='棚へ箱を置いてください。',
        reference=None, question_meta={'core_propositions': ['棚へ箱を置く。']},
        session_id=None, semantic_judge=judge)
    assert events == ['semantic', 'diagnostic']
    assert result['verdict'] == 'accept'
    assert result['C'] == 1.0
    assert result['legacy_metrics']['errors'] == ['legacy_diagnostic_error:RuntimeError']


def test_explicit_requirements_need_no_generated_legacy_trap(monkeypatch, semantic_complete_judge):
    def forbidden(*args, **kwargs):
        raise AssertionError('No metadata API should run for explicit requirements')

    monkeypatch.setattr('experiments.meta_generator.generate_meta', forbidden)
    result = pipeline.run_audit(question='Q', response='棚へ箱を置いてください。',
        reference=None, question_meta={'core_propositions': ['棚へ箱を置く。']},
        session_id=None, auto_generate_meta=True, semantic_judge=semantic_complete_judge)
    assert result['verdict'] == 'accept'
    assert result['metadata_source'] == 'inline'


def test_fallback_requirement_extraction_cannot_certify_accept(monkeypatch, semantic_complete_judge):
    monkeypatch.setattr('experiments.meta_generator.generate_meta', lambda question: {
        'core_propositions': ['棚へ箱を置く。'], '_is_fallback': True,
    })
    result = pipeline.run_audit(question='Q', response='棚へ箱を置いてください。',
        reference=None, question_meta=None, session_id=None, auto_generate_meta=True,
        semantic_judge=semantic_complete_judge)
    assert result['verdict'] == 'degraded'
    assert result['semantic_matching']['requirements_total'] == 1
    assert result['semantic_matching']['unknown_count'] == 1
