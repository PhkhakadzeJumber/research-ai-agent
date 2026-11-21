"""Table-driven tests for the novelty verdict rules (src/ai/evidence/verdicts.py, doc 07 §4.1)."""

import pytest

from src.ai.evidence.verdicts import (
    FeasibilityInputs,
    MatchFact,
    NoveltyInputs,
    SignificanceInputs,
    VerdictResult,
    feasibility_verdict,
    novelty_verdict,
    recommendation,
    significance_verdict,
    triage_rule,
)

KEYS = (1, 2, 3)  # problem, method, data setting
THOROUGH = dict(relevant=60, screened=300, read=30, sources_ok=3)


def row(paper, *overlaps, verified=True, facets=KEYS):
    return tuple(MatchFact(paper, facet, overlap, verified) for facet, overlap in zip(facets, overlaps))


def inputs(*matches, depth="full", minor=(), gaps=(), skeptic_saturated=True, skeptic_ran=True, **overrides):
    values = {**THOROUGH, **overrides}
    return NoveltyInputs(depth=depth, key_facets=KEYS, matches=tuple(m for group in matches for m in group),
                         minor_difference_papers=frozenset(minor), gap_claim_statuses=tuple(gaps),
                         skeptic_saturated=skeptic_saturated, skeptic_ran=skeptic_ran, **values)


@pytest.mark.parametrize(("case", "expected_category", "expected_rule"), [
    # N1: verified "same" on every key facet in one paper
    (inputs(row("p1", "same", "same", "same")), "previously_investigated", "N1"),
    # "same" everywhere but spread over two papers: no single paper did it all
    (inputs(row("p1", "same", "same", "different"), row("p2", "different", "different", "same")),
     "supported_opportunity", "N3"),
    # an unverified quote never counts
    (inputs(row("p1", "same", "same", "same", verified=False)), "supported_opportunity", "N3"),
    # N2: same, same, similar + marked minor
    (inputs(row("p1", "same", "same", "similar"), minor=["p1"]), "incremental_variation", "N2"),
    # same, same, similar but NOT marked minor: no incremental signal
    (inputs(row("p1", "same", "same", "similar")), "supported_opportunity", "N3"),
    # two "similar": too far for incremental
    (inputs(row("p1", "same", "similar", "similar"), minor=["p1"]), "supported_opportunity", "N3"),
    # N0 thresholds and failures
    (inputs(row("p1", "same", "same", "same"), relevant=19), "insufficient_evidence", "N0"),
    (inputs(assessment_failed=True), "insufficient_evidence", "N0"),
    (inputs(retrieval_failed=True), "insufficient_evidence", "N0"),
    # N4: no matching work, but the search has limits
    (inputs(sources_ok=2), "potential_opportunity", "N4"),
    (inputs(skeptic_saturated=False, skeptic_ran=False), "potential_opportunity", "N4"),
    (inputs(gaps=["verified", "unverified_quote"]), "potential_opportunity", "N4"),
    (inputs(depth="light"), "potential_opportunity", "N4"),
])
def test_novelty_rules(case, expected_category, expected_rule):
    verdict = novelty_verdict(case)
    assert (verdict.category, verdict.rule) == (expected_category, expected_rule)
    assert verdict.rules_version == "novelty-rules-v2" and verdict.trace


def test_previously_investigated_names_its_papers():
    verdict = novelty_verdict(inputs(row("p2", "same", "same", "same"), row("p1", "same", "same", "same")))
    assert verdict.papers == ["p1", "p2"] and verdict.label == "Previously investigated"


def test_weaknesses_list_every_unmet_condition():
    verdict = novelty_verdict(inputs(screened=100, read=24, sources_ok=2, skeptic_saturated=False,
                                     skeptic_ran=False, gaps=["no_evidence"]))
    assert verdict.weaknesses == [
        "screened: 100 (at least 150 needed)", "read: 24 (at least 25 needed)", "sources: 2 (at least 3 needed)",
        "the Skeptic hasn't reviewed the idea yet", "1 gap claim(s) without verified evidence",
    ]


def test_the_current_project_cannot_reach_supported_with_two_sources():
    # Today: arXiv + OpenAlex only, and no Skeptic yet -> at best Potential, honestly.
    verdict = novelty_verdict(inputs(sources_ok=2, skeptic_saturated=False, skeptic_ran=False))
    assert verdict.category == "potential_opportunity"


def test_insufficient_lists_every_reason():
    verdict = novelty_verdict(inputs(relevant=5, retrieval_failed=True))
    assert verdict.trace == ["N0: retrieval failed; only 5 relevant papers found (at least 20 needed)"]


def test_incremental_notes_that_the_skeptic_has_not_looked():
    verdict = novelty_verdict(inputs(row("p1", "same", "same", "similar"), minor=["p1"], skeptic_ran=False,
                                     skeptic_saturated=False))
    assert verdict.category == "incremental_variation" and "Skeptic hasn't reviewed" in verdict.trace[-1]


def test_a_candidate_needs_key_facets():
    with pytest.raises(ValueError):
        novelty_verdict(NoveltyInputs(depth="full", key_facets=(), matches=()))



def test_the_skeptic_override_wins_over_the_matrix():
    verdict = novelty_verdict(inputs(row("p1", "different", "different", "different"),
                                     skeptic_override_papers=frozenset({"p9"})))
    assert (verdict.category, verdict.rule, verdict.papers) == ("previously_investigated", "N1S", ["p9"])


def test_too_little_evidence_still_comes_first():
    verdict = novelty_verdict(inputs(relevant=3, skeptic_override_papers=frozenset({"p9"})))
    assert verdict.rule == "N0"



# --- significance (step 4.8) ------------------------------------------------------------------------------

def sig(*claims, surveys=(), novelty="potential_opportunity", overstated=0):
    return significance_verdict(SignificanceInputs(tuple(frozenset(c) for c in claims), frozenset(surveys), novelty,
                                                   overstated))


@pytest.mark.parametrize(("verdict", "category", "rule"), [
    (sig({"p1"}, {"p2"}), "strong", "S2"),                       # two claims, two papers
    (sig({"p1", "p2"}), "strong", "S2"),                          # one claim resting on two papers
    (sig({"p1"}, {"p1"}), "moderate", "S3"),                      # two claims, one paper
    (sig({"p1"}, surveys={"p1"}), "strong", "S2"),                # a survey counts
    (sig(), "unclear", "S4"),
    (sig({"p1"}, {"p2"}, novelty="incremental_variation"), "weak", "S1"),
    (sig({"p1"}, {"p2"}, overstated=1), "moderate", "S2+S5"),     # one step down
    (sig({"p1"}, overstated=1), "weak", "S3+S5"),
    (sig(overstated=1), "unclear", "S4"),
])
def test_significance_rules(verdict, category, rule):
    assert (verdict.category, verdict.rule, verdict.rules_version) == (category, rule, "significance-rules-v1")


# --- feasibility ------------------------------------------------------------------------------------------

def feas(blocking=(), simplifications=(), gaps=(), unconfirmed=()):
    return feasibility_verdict(FeasibilityInputs(tuple(blocking), tuple(simplifications), tuple(gaps),
                                                 tuple(unconfirmed)))


@pytest.mark.parametrize(("verdict", "category", "rule"), [
    (feas(), "feasible", "F4"),
    (feas(blocking=["needs an A100"]), "not_feasible", "F1"),
    (feas(blocking=["needs an A100"], simplifications=["use a 1B model"]), "feasible_with_adjustments", "F2"),
    (feas(gaps=["PyTorch"]), "feasible_with_adjustments", "F2"),
    (feas(unconfirmed=["GeoQA"]), "unclear", "F3"),
    (feas(blocking=["x"], simplifications=["y"], unconfirmed=["GeoQA"]), "feasible_with_adjustments", "F2"),
])
def test_feasibility_rules(verdict, category, rule):
    assert (verdict.category, verdict.rule, verdict.rules_version) == (category, rule, "feasibility-rules-v1")


# --- recommendation and triage ----------------------------------------------------------------------------

def v(category, weaknesses=()):
    return VerdictResult(category, "x", ["x"], weaknesses=list(weaknesses))


@pytest.mark.parametrize(("novelty", "significance", "feasibility", "refine", "expected", "rule", "flag"), [
    ("potential_opportunity", "moderate", "feasible", False, "pursue", "R2", False),
    ("supported_opportunity", "strong", "feasible_with_adjustments", False, "pursue", "R2", False),
    ("previously_investigated", "strong", "feasible", True, "reconsider", "R1", False),
    ("potential_opportunity", "weak", "feasible", True, "reconsider", "R1", False),
    ("potential_opportunity", "moderate", "not_feasible", True, "reconsider", "R1", False),
    ("potential_opportunity", "moderate", "unclear", True, "refine", "R3", True),      # one short + refinement
    ("potential_opportunity", "moderate", "unclear", False, "reconsider", "R4", True),  # nothing to refine
    ("incremental_variation", "unclear", "feasible", True, "reconsider", "R4", True),   # two short
])
def test_recommendation_rules(novelty, significance, feasibility, refine, expected, rule, flag):
    rec = recommendation(v(novelty), v(significance), v(feasibility), refinement_available=refine)
    assert (rec.category, rec.rule, rec.needs_further_investigation) == (expected, rule, flag)


def test_potential_with_search_limits_is_flagged_but_can_still_be_pursued():
    rec = recommendation(v("potential_opportunity", ["sources: 2 (at least 3 needed)"]), v("moderate"), v("feasible"),
                         refinement_available=False)
    assert (rec.category, rec.needs_further_investigation) == ("pursue", True)


@pytest.mark.parametrize(("novelty", "significance", "feasibility", "simplify", "open_facets", "depth", "number",
                          "action"), [
    ("potential_opportunity", "moderate", "not_feasible", False, False, "full", 1, "reject"),
    ("previously_investigated", "strong", "feasible", False, True, "full", 2, "refine"),
    ("previously_investigated", "strong", "feasible", False, False, "full", 3, "reject"),
    ("incremental_variation", "weak", "feasible", False, False, "full", 4, "refine"),
    ("potential_opportunity", "moderate", "feasible_with_adjustments", True, False, "full", 9, "finalize"),
    ("potential_opportunity", "weak", "feasible", False, False, "full", 6, "reject"),
    ("insufficient_evidence", "unclear", "feasible", False, False, "full", 7, "expand_search"),
    ("potential_opportunity", "moderate", "feasible", False, False, "light", 9, "advance"),
    ("potential_opportunity", "moderate", "feasible", False, False, "full", 9, "finalize"),
])
def test_triage_rules_first_match_wins(novelty, significance, feasibility, simplify, open_facets, depth, number,
                                       action):
    rule = triage_rule(v(novelty), v(significance), v(feasibility), simplifications=simplify,
                       open_facets=open_facets, depth=depth)
    assert (rule.number, rule.action) == (number, action)


def test_insufficient_evidence_without_budget_is_finalized_flagged():
    rule = triage_rule(v("insufficient_evidence"), v("unclear"), v("feasible"), simplifications=False,
                       open_facets=False, depth="full", budget_left=False)
    assert (rule.number, rule.action) == (7, "finalize")



def test_rule_5_needs_something_that_blocks():
    # step 4.8 live run: skill gaps alone made rule 5 fire and contradict "Pursue"
    adjusted = v("feasible_with_adjustments", ["skill gap: reading Georgian"])
    common = dict(simplifications=True, open_facets=False, depth="full")
    assert triage_rule(v("potential_opportunity"), v("moderate"), adjusted, **common).number == 9
    blocked = triage_rule(v("potential_opportunity"), v("moderate"), adjusted, blocking=True, **common)
    assert (blocked.number, blocked.action) == (5, "refine")
