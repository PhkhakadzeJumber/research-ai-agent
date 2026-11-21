"""The verdict engine (doc 07 §4): code, not a model, decides the verdicts.

So far: novelty (doc 07 §4.1), rule set NOVELTY_RULES_VERSION. The rules, in order; the
first that holds decides:

  N0 Insufficient evidence    the novelty assessment failed, retrieval failed, or fewer than
                              MIN_RELEVANT papers were judged relevant (score 2-3)
  N1 Previously investigated  one paper is a verified "same" on EVERY key facet; or (N1S, the
                              Skeptic override, doc 07 §4.5) a prior_work_found challenge has a
                              verified quote, names "same" on every key facet, and was not
                              rebutted with verified evidence
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

NOVELTY_RULES_VERSION = "novelty-rules-v2"  # v2 (step 4.7b): the Skeptic override N1S

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
    skeptic_override_papers: frozenset[str] = frozenset()
    """Papers cited by prior_work_found challenges that qualify for N1S (checked by the caller)."""


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

    # N1S: the Skeptic's override
    if inputs.skeptic_override_papers:
        return VerdictResult("previously_investigated", "N1S",
                             ["N1S: a Skeptic challenge with verified quotes shows 'same' on every key facet "
                              f"{list(keys)}, not rebutted with evidence"],
                             papers=sorted(inputs.skeptic_override_papers))

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


# --- significance (doc 07 §4.2, §4.5) ---------------------------------------------------------------------

SIGNIFICANCE_RULES_VERSION = "significance-rules-v1"
SIGNIFICANCE_LEVELS = ("weak", "moderate", "strong")  # ordered, for the one-step drop
MARGINAL_NOVELTY = ("previously_investigated", "incremental_variation")


@dataclass(frozen=True)
class SignificanceInputs:
    claim_papers: tuple[frozenset[str], ...]
    """For each VERIFIED significance claim: the papers whose verified quotes support it."""
    survey_papers: frozenset[str]
    """Papers the Paper Reader classified as surveys."""
    novelty_category: str
    overstated_unrebutted: int = 0
    """Skeptic 'overstated_contribution' challenges not rebutted with evidence."""


def significance_verdict(inputs: SignificanceInputs) -> VerdictResult:
    """S1 Weak: the novelty verdict makes the contribution marginal (previously investigated or
    incremental). S2 Strong: >= 2 verified claims resting on different papers, or one resting on a
    survey. S3 Moderate: >= 1 verified claim. S4 Unclear: no verified evidence either way.
    Then S5: an overstated-contribution challenge not rebutted with evidence drops the level one step."""
    papers = frozenset().union(*inputs.claim_papers) if inputs.claim_papers else frozenset()
    surveys = papers & inputs.survey_papers
    if inputs.novelty_category in MARGINAL_NOVELTY:
        category, rule = "weak", "S1"
        trace = [f"S1: novelty is {inputs.novelty_category}: the contribution is marginal"]
    elif len(papers) >= 2 or surveys:
        category, rule = "strong", "S2"
        trace = [f"S2: {len(inputs.claim_papers)} verified claim(s) from {len(papers)} paper(s)"
                 + (" incl. a survey" if surveys else "")]
    elif inputs.claim_papers:
        category, rule = "moderate", "S3"
        trace = ["S3: one paper documents the problem as relevant"]
    else:
        return VerdictResult("unclear", "S4", ["S4: no verified evidence that the problem matters"],
                             rules_version=SIGNIFICANCE_RULES_VERSION)
    if inputs.overstated_unrebutted and category != "weak":
        category = SIGNIFICANCE_LEVELS[SIGNIFICANCE_LEVELS.index(category) - 1]
        trace.append(f"S5: {inputs.overstated_unrebutted} overstated-contribution challenge(s) not rebutted: "
                     f"one step down to {category}")
        rule = f"{rule}+S5"
    return VerdictResult(category, rule, trace, papers=sorted(papers), rules_version=SIGNIFICANCE_RULES_VERSION)


# --- feasibility (doc 07 §4.3) ----------------------------------------------------------------------------

FEASIBILITY_RULES_VERSION = "feasibility-rules-v1"


@dataclass(frozen=True)
class FeasibilityInputs:
    blocking: tuple[str, ...]
    """A6's blocking requirements plus code's own (e.g. the timeline over the available time)."""
    simplifications: tuple[str, ...]
    skill_gaps: tuple[str, ...]
    unconfirmed_data: tuple[str, ...]
    """Required data not confirmed available (not found in the papers read)."""


def feasibility_verdict(inputs: FeasibilityInputs) -> VerdictResult:
    """F1 Not feasible under your constraints: something blocks and no simplification is offered.
    F2 Feasible with adjustments: something blocks (with simplifications), or skills are missing.
    F3 Unclear: required data is not confirmed available. F4 Feasible: none of the above.
    The blocking requirements always override the level (doc 07 §4.3)."""
    version = FEASIBILITY_RULES_VERSION
    if inputs.blocking and not inputs.simplifications:
        return VerdictResult("not_feasible", "F1",
                             [f"F1: blocked ({'; '.join(inputs.blocking)}), no simplification offered"],
                             weaknesses=list(inputs.blocking), rules_version=version)
    if inputs.blocking or inputs.skill_gaps:
        reasons = [*inputs.blocking, *(f"skill gap: {gap}" for gap in inputs.skill_gaps)]
        return VerdictResult("feasible_with_adjustments", "F2", [f"F2: adjustments needed: {'; '.join(reasons)}"],
                             weaknesses=reasons, rules_version=version)
    if inputs.unconfirmed_data:
        return VerdictResult("unclear", "F3",
                             [f"F3: data not confirmed available: {', '.join(inputs.unconfirmed_data)}"],
                             weaknesses=[f"unconfirmed data: {name}" for name in inputs.unconfirmed_data],
                             rules_version=version)
    return VerdictResult("feasible", "F4", ["F4: nothing blocks, the data is confirmed, the skills are in place"],
                         rules_version=version)


# --- recommendation (doc 07 §4.4) and triage (doc 05 §4) --------------------------------------------------

RECOMMENDATION_RULES_VERSION = "recommendation-rules-v1"
LABELS = {
    **CATEGORY_LABELS,
    "strong": "Strong", "moderate": "Moderate", "weak": "Weak", "unclear": "Unclear",
    "feasible": "Feasible", "feasible_with_adjustments": "Feasible with adjustments",
    "not_feasible": "Not feasible under your constraints",
    "pursue": "Pursue", "refine": "Refine", "reconsider": "Reconsider",
}


@dataclass(frozen=True)
class Recommendation:
    category: str
    rule: str
    trace: list[str]
    needs_further_investigation: bool
    rules_version: str = RECOMMENDATION_RULES_VERSION

    @property
    def label(self) -> str:
        return LABELS[self.category]


def recommendation(novelty: VerdictResult, significance: VerdictResult, feasibility: VerdictResult, *,
                   refinement_available: bool) -> Recommendation:
    """R1 Reconsider: novelty previously investigated, or not feasible, or significance weak.
    R2 Pursue: novelty supported or potential, significance strong or moderate, feasibility
    feasible (with or without adjustments). R3 Refine: exactly one dimension falls short and a
    concrete refinement exists. R4 Reconsider: otherwise (several short, or nothing to refine).
    The 'needs further investigation' flag (doc 07 §4.4) goes with any of them when novelty is
    insufficient or potential with search limits, or a dimension is unclear."""
    flag = (novelty.category == "insufficient_evidence"
            or (novelty.category == "potential_opportunity" and bool(novelty.weaknesses))
            or "unclear" in (significance.category, feasibility.category))
    reasons = []
    if novelty.category == "previously_investigated":
        reasons.append("novelty: previously investigated")
    if feasibility.category == "not_feasible":
        reasons.append("not feasible under your constraints")
    if significance.category == "weak":
        reasons.append("significance: weak")
    if reasons:
        return Recommendation("reconsider", "R1", [f"R1: {'; '.join(reasons)}"], flag)

    short = []
    if novelty.category not in ("supported_opportunity", "potential_opportunity"):
        short.append(f"novelty: {LABELS[novelty.category]}")
    if significance.category not in ("strong", "moderate"):
        short.append(f"significance: {LABELS[significance.category]}")
    if feasibility.category not in ("feasible", "feasible_with_adjustments"):
        short.append(f"feasibility: {LABELS[feasibility.category]}")
    if not short:
        return Recommendation("pursue", "R2", ["R2: every dimension is good enough"], flag)
    if len(short) == 1 and refinement_available:
        return Recommendation("refine", "R3", [f"R3: one dimension falls short ({short[0]}); a refinement exists"],
                              flag)
    why = "several dimensions fall short" if len(short) > 1 else "no concrete refinement exists"
    return Recommendation("reconsider", "R4", [f"R4: {why} ({'; '.join(short)})"], flag)


@dataclass(frozen=True)
class TriageRule:
    number: int
    action: str
    """reject / refine / expand_search / advance / finalize (doc 10 §7.6)."""
    reason: str


def triage_rule(novelty: VerdictResult, significance: VerdictResult, feasibility: VerdictResult, *,
                simplifications: bool, open_facets: bool, depth: str, budget_left: bool = True,
                blocking: bool = False) -> TriageRule:
    """The first matching triage rule of doc 05 §4. Rule 8 (a refuted key claim: re-run the novelty
    assessment) already happens inside the evaluation, in the Skeptic loop (step 4.7b).
    Rule 5 needs a blocking requirement: skill gaps alone are adjustments, not a reason to
    simplify the idea (step 4.8 live run: it fired on skill gaps and contradicted "Pursue")."""
    if feasibility.category == "not_feasible":
        return TriageRule(1, "reject", "infeasible under your constraints")
    if novelty.category == "previously_investigated":
        if open_facets:
            return TriageRule(2, "refine", "previously investigated, but the closest work leaves a facet open")
        return TriageRule(3, "reject", "previously investigated, no open facet")
    if novelty.category == "incremental_variation":
        return TriageRule(4, "refine", "incremental variation: refine once to increase the contribution")
    if feasibility.category == "feasible_with_adjustments" and blocking and simplifications:
        return TriageRule(5, "refine", "simplify to fit your constraints, then re-check feasibility and novelty")
    if significance.category == "weak":
        return TriageRule(6, "reject", "significance is weak")
    if novelty.category == "insufficient_evidence":
        if budget_left:
            return TriageRule(7, "expand_search", "insufficient evidence: expand the search once")
        return TriageRule(7, "finalize", "insufficient evidence and no budget left: flagged")
    return TriageRule(9, "advance" if depth == "light" else "finalize", "nothing blocks")
