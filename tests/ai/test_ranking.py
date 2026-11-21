"""Tests for the ordering rules (src/ai/evidence/ranking.py). Pure code."""

from src.ai.evidence.ranking import Standing, ranking_key, reasons


def s(rec="pursue", nov="potential_opportunity", feas="feasible", sig="moderate", quotes=0):
    return Standing(rec, nov, feas, sig, quotes)


def ranked(*standings):
    return sorted(standings, key=ranking_key)


def test_each_rule_in_order():
    assert ranked(s(rec="refine"), s(rec="pursue"))[0].recommendation == "pursue"
    assert ranked(s(nov="potential_opportunity"), s(nov="supported_opportunity"))[0].novelty == "supported_opportunity"
    assert ranked(s(feas="feasible_with_adjustments"), s(feas="feasible"))[0].feasibility == "feasible"
    assert ranked(s(sig="weak"), s(sig="unclear"), s(sig="strong"))[0].significance == "strong"
    assert ranked(s(quotes=2), s(quotes=5))[0].supporting_quotes == 5


def test_an_earlier_rule_wins_over_later_ones():
    pursue_weak = s(rec="pursue", sig="weak")
    refine_strong = s(rec="refine", nov="supported_opportunity", sig="strong", quotes=9)
    assert ranked(refine_strong, pursue_weak)[0] is pursue_weak


def test_missing_verdicts_sort_last_and_reasons_are_given():
    assert ranked(s(rec=None), s(rec="reconsider"))[0].recommendation == "reconsider"
    assert reasons(s(rec="pursue"), s(rec="refine")) == "recommendation: pursue before refine"
    assert reasons(s(feas="feasible"), s(feas="feasible_with_adjustments")) == (
        "feasibility: feasible before feasible with adjustments")
    assert reasons(s(quotes=3), s(quotes=1)) == "evidence: 3 verified supporting quotes before 1"
    assert reasons(s(), s()) == "tied on every rule"
