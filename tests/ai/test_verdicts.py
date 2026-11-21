"""Table-driven tests for the novelty verdict rules (src/ai/evidence/verdicts.py, doc 07 §4.1)."""

import pytest

from src.ai.evidence.verdicts import MatchFact, NoveltyInputs, novelty_verdict

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
    assert verdict.rules_version == "novelty-rules-v1" and verdict.trace


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
