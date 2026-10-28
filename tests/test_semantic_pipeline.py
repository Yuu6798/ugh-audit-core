"""Known real-response replay plus authoritative public Python/CLI contracts."""
import copy
import json
from pathlib import Path

import pytest

from audit import audit
from detector import detect
from ugh_audit.pipeline import run_audit
from ugh_audit.semantic_matching import MODEL


FIXTURE=Path(__file__).with_name('fixtures')/'semantic_v2_recorded.jsonl'
CASES=[json.loads(line) for line in FIXTURE.read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('case',CASES,ids=lambda case:case['id'])
def test_all_64_known_answers_preserve_real_v2_statuses_and_veto_bad_accept(case):
    calls=[]
    def recorded(requirement,answer,*,timeout_seconds):
        assert answer==case['response']
        index=case['requirements'].index(requirement)
        calls.append(index)
        return copy.deepcopy(case['recorded_results'][index])
    meta={'id':case['id'],'question':case['question'],
          'core_propositions':case['requirements'],'trap_type':''}
    result=run_audit(question=case['question'],response=case['response'],reference=None,
                     question_meta=meta,session_id='recorded-regression',
                     detect_fn=detect,semantic_judge=recorded)
    assert calls==[0,1,2]
    semantic=result['semantic_matching']
    assert [row['status'] for row in semantic['results']]==case['expected_statuses']
    assert semantic['unknown_count']==0
    if 'missing' in case['expected_statuses']:
        assert result['requirement_verdict']=='incomplete'
        assert result['verdict']!='accept'
        assert result['verdict_advisory']!='accept'
        assert result['requires_review'] is True
    else:
        assert result['requirement_verdict']=='complete'
        assert result['verdict']=='accept'
        assert result['C']==1.0
        assert result['structural_gate']['binding'] is False


def test_public_python_audit_without_provider_cannot_return_accept():
    result=audit('direct','受付へ書類を提出してください。',
                 {'question':'書類提出の指示','core_propositions':['受付へ書類を提出する'],
                  'trap_type':''},semantic_judge=None)
    assert result['policy']['decision']=='degraded'
    assert result['requirement_verdict']=='undetermined'
    assert result['state']['C'] is None
    assert result['requires_review'] is True


def test_public_python_audit_partial_requirement_never_accepts(semantic_complete_judge):
    meta={'question':'資料の用意と提出について指示してください。',
          'core_propositions':['資料を3部用意する','受付へ提出する','正午までに提出する'],
          'trap_type':''}
    def judge(requirement,answer,*,timeout_seconds):
        if requirement!='正午までに提出する':
            return semantic_complete_judge(requirement,answer,timeout_seconds=timeout_seconds)
        value={'status':'missing','missing_kind':'absence','evidence':[],
               'reason':'提出期限が書かれていない'}
        return {**value,'unknown_source':None,'api_called':True,'requested_model':MODEL,
                'returned_model':MODEL,'stop_reason':'end_turn','raw_response':json.dumps(value)}
    result=audit('partial','資料を3部用意し、受付へ提出してください。',meta,semantic_judge=judge)
    assert result['requirement_verdict']=='incomplete'
    assert result['policy']['decision']!='accept'
    assert 'ADD_PROPOSITION' in result['policy']['repair_order']
    assert result['budget']['total_cost']>0


def test_semantic_complete_accepts_despite_unknown_optional_diagnostic(semantic_complete_judge):
    result=audit('unknown-structure','受付へ書類を提出してください。',
                 {'question':'提出方法','core_propositions':['受付へ書類を提出する']},
                 semantic_judge=semantic_complete_judge)
    assert result['requirement_verdict']=='complete'
    assert result['policy']['decision']=='accept'
    assert result['requires_review'] is False
