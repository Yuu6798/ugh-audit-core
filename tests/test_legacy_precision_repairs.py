"""Narrow legacy bug regressions; not a general semantic matcher guarantee."""
from dataclasses import replace
from fractions import Fraction

import pytest

from detector import check_propositions
from ugh_calculator import Evidence, State, calculate, derive_verdict


@pytest.mark.parametrize('answer', [
    '社内資料を外部へ送付する。',
    '社内資料を外部へ送付する。追加の案内はありません。',
    '社内資料を外部へ送付するが、追加の案内はありません。',
    '社内資料を外部へ送付するかもしれない。',
])
def test_normal_overlap_cannot_erase_ordinary_negation(answer):
    requirement = '社内資料を外部へ送付しない。'
    assert check_propositions(answer, [requirement]) == (0, [], [0])


@pytest.mark.parametrize('answer', [
    '社内資料を外部へ送付しない。',
    '社内資料を外部へ送付しません。',
])
def test_actual_scoped_negation_still_hits(answer):
    assert check_propositions(answer, ['社内資料を外部へ送付しない。']) == (1, [0], [])


def test_positive_and_negative_deontic_controls_are_unchanged():
    positive = '社内資料を外部へ送付する。'
    negative = '社内資料を外部へ送付すべきではない。'
    assert check_propositions(positive, [positive]) == (1, [0], [])
    assert check_propositions(positive, [negative]) == (0, [], [0])
    assert check_propositions(negative, [negative]) == (1, [0], [])


@pytest.mark.parametrize('requirement,answer', [
    ('低ΔEは良い回答を保証しない',
     'ΔEが低いだけでは回答品質は保証されないため、複合評価が重要である'),
    ('ICLの完全なメカニズムは未解明',
     'ICLのメカニズムについては未だ完全には解明されていない研究課題である'),
])
def test_passive_and_progressive_negative_paraphrases_still_hit(requirement, answer):
    assert check_propositions(answer, [requirement]) == (1, [0], [])


@pytest.mark.parametrize('flag', ['f1_anchor', 'f3_operator'])
def test_pre_round_rewrite_bin_cannot_round_into_accept(flag):
    evidence = Evidence(question_id='rounding', propositions_total=13,
                        propositions_hit=6, f4_premise=None, **{flag: .5})
    # Independent rational arithmetic for this known boundary case:
    # S=13/14, C=6/13, exact ΔE=1657/16562 > 1/10.
    exact = (2*(1-Fraction(13, 14))**2 + (1-Fraction(6, 13))**2)/3
    assert exact == Fraction(1657, 16562) and exact > Fraction(1, 10)
    state = calculate(evidence)
    assert state.delta_e == .10
    assert state.delta_e_bin == 2
    assert derive_verdict(state) == 'rewrite'


def direct_state(delta, bin_value):
    return State(S=1., C=1., delta_e=delta, quality_score=5.,
                 delta_e_bin=bin_value, C_bin=3, por_state='inactive')


def test_pre_round_regenerate_bin_cannot_round_into_rewrite():
    assert derive_verdict(direct_state(.25, 3)) == 'regenerate'


@pytest.mark.parametrize('delta,bin_value,expected', [
    (.10, 1, 'accept'), (.25, 2, 'rewrite'),
    (.11, 1, 'rewrite'), (.26, 1, 'regenerate'),
    (.09, 3, 'accept'), (.24, 3, 'rewrite'),
    (.10, None, 'accept'), (.10, 2., 'accept'), (.10, '2', 'accept'),
    (.10, 3, 'accept'), (.25, 1, 'rewrite'),
    (None, 2, 'degraded'),
])
def test_direct_state_scalar_compatibility_and_verified_boundary_bins(delta, bin_value, expected):
    assert derive_verdict(direct_state(delta, bin_value)) == expected


def test_calculated_states_keep_pre_round_bins_across_the_full_discrete_grid():
    verdict_by_bin = {1: 'accept', 2: 'rewrite', 3: 'regenerate'}
    checked = 0
    for f1 in (0., .5, 1.):
        for f2 in (0., .5, 1.):
            for f3 in (0., .5, 1.):
                for f4 in (0., .5, 1., None):
                    for total in range(1, 17):
                        base = Evidence(question_id='grid', f1_anchor=f1, f2_unknown=f2,
                                        f3_operator=f3, f4_premise=f4, propositions_total=total)
                        for hits in range(total+1):
                            state = calculate(replace(base, propositions_hit=hits))
                            assert derive_verdict(state) == verdict_by_bin[state.delta_e_bin]
                            checked += 1
    assert checked == 16416
