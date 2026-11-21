"""The verdict engine (doc 07 §4): code, not a model, decides the verdicts.

So far: novelty (doc 07 §4.1), rule set NOVELTY_RULES_VERSION. The rules, in order; the
first that holds decides:

  N0 Insufficient evidence    the novelty assessment failed, retrieval failed, or fewer than
                              MIN_RELEVANT papers were judged relevant (score 2-3)
  N1 Previously investigated  one paper is a verified "same" on EVERY key facet
  N2 Incremental variation    one paper is a verified "same" on all key facets but one, a
                              verified "similar" on that one, and the Novelty Investigator
                              marked the difference as minor
  N3 Supported opportunity    full depth; coverage >= thresholds (screened, read, sources);
                              the Skeptic reached saturation; every gap claim verified
  N4 Potential opportunity    otherwise, with every unmet N3 condition listed as a weakness

"Verified" means the cell's quote was verified against the stored paper text: a "same"
without a verified quote never counts. Light evaluations can't reach N3 (doc 07 §4.1).
Every verdict carries a rule trace (which rule fired, on which papers and claims).
"""

from __future__ import annotations

from dataclasses import dataclass, field

NOVELTY_RULES_VERSION = "novelty-rules-v1"

MIN_RELEVANT = 20
SUPPORTED_THRESHOLDS = {"screened": 150, "read": 25, "sources": 3}  # doc 07 §4.1 defaults

CATEGORY_LABELS = {
    "insufficient_evidence": "Insufficient evidence",
    "previously_investigated": "Previously investigated",
    "incremental_variation": "Incremental variation",
    "supported_opportunity": "Supported opportunity",
    "potential_opportunity": "Potential opportunity",
}


@dataclass(frozen=True)
class MatchFact:
    paper: str
    """A paper ID (as text)."""
    facet: int
    """The facet's number (position + 1)."""
    overlap: str
    verified: bool


@dataclass(frozen=True)
class NoveltyInputs:
    depth: str
    key_facets: tuple[int, ...]
    matches: tuple[MatchFact, ...]
    minor_difference_papers: frozenset[str] = frozenset()
    gap_claim_statuses: tuple[str, ...] = ()
    relevant: int = 0
    """Papers scored 2-3 in screening."""
    screened: int = 0
    read: int = 0
    sources_ok: int = 0
    retrieval_failed: bool = False
    assessment_failed: bool = False
    skeptic_saturated: bool = False
    skeptic_ran: bool = False


@dataclass(frozen=True)
class VerdictResult:
    category: str
    rule: str
    trace: list[str]
    weaknesses: list[str] = field(default_factory=list)
    papers: list[str] = field(default_factory=list)
    """The papers the deciding rule rests on (closest work for N1/N2)."""
    rules_version: str = NOVELTY_RULES_VERSION

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self.category]


def _verified_cells(inputs: NoveltyInputs) -> dict[str, dict[int, str]]:
    """paper -> facet -> overlap, for cells whose quote was verified."""
    cells: dict[str, dict[int, str]] = {}
    for match in inputs.matches:
        if match.verified:
            cells.setdefault(match.paper, {})[match.facet] = match.overlap
    return cells


def novelty_verdict(inputs: NoveltyInputs) -> VerdictResult:
    keys = inputs.key_facets
    if not keys:
        raise ValueError("A candidate needs at least one key facet.")

    # N0
    reasons = []
    if inputs.assessment_failed:
        reasons.append("the novelty assessment could not be made")
    if inputs.retrieval_failed:
        reasons.append("retrieval failed")
    if inputs.relevant < MIN_RELEVANT:
        reasons.append(f"only {inputs.relevant} relevant papers found (at least {MIN_RELEVANT} needed)")
    if reasons:
        return VerdictResult("insufficient_evidence", "N0", [f"N0: {'; '.join(reasons)}"])

    cells = _verified_cells(inputs)

    # N1: a verified "same" on every key facet
    same_on_all = sorted(paper for paper, row in cells.items() if all(row.get(k) == "same" for k in keys))
    if same_on_all:
        return VerdictResult("previously_investigated", "N1",
                             [f"N1: verified 'same' on every key facet {list(keys)} in {len(same_on_all)} paper(s)"],
                             papers=same_on_all)

    # N2: same on all key facets but one, similar on that one, difference marked minor
    incremental = []
    for paper, row in cells.items():
        labels = [row.get(k) for k in keys]
        if (labels.count("similar") == 1 and labels.count("same") == len(keys) - 1
                and paper in inputs.minor_difference_papers):
            incremental.append(paper)
    if incremental:
        trace = [f"N2: verified 'same' on all key facets but one ('similar') in {len(incremental)} paper(s), "
                 "difference marked minor"]
        if not inputs.skeptic_ran:
            trace.append("N2: the Skeptic hasn't reviewed the difference yet")
        return VerdictResult("incremental_variation", "N2", trace, papers=sorted(incremental))

    # N3 / N4: no matching work; how strong is the search behind that?
    weaknesses = []
    if inputs.depth != "full":
        weaknesses.append("light evaluation: only a full evaluation can support an opportunity")
    for name, minimum in SUPPORTED_THRESHOLDS.items():
        value = {"screened": inputs.screened, "read": inputs.read, "sources": inputs.sources_ok}[name]
        if value < minimum:
            weaknesses.append(f"{name}: {value} (at least {minimum} needed)")
    if not inputs.skeptic_saturated:
        weaknesses.append("the Skeptic hasn't reached saturation" if inputs.skeptic_ran
                          else "the Skeptic hasn't reviewed the idea yet")
    unverified_gaps = sum(1 for status in inputs.gap_claim_statuses if status not in ("verified", "defended"))
    if unverified_gaps:
        weaknesses.append(f"{unverified_gaps} gap claim(s) without verified evidence")

    if not weaknesses:
        return VerdictResult("supported_opportunity", "N3",
                             ["N3: no matching work on the key facets under thorough search"])
    return VerdictResult("potential_opportunity", "N4",
                         ["N4: no verified 'same' on the key facets combined, but the search has limits"],
                         weaknesses=weaknesses)
