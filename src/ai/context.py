"""Context packets: exactly what an agent sees, built by code (doc 08 §5). Papers for the
Paper Reader (render_paper); ideas, revisions and query-planning tasks for the Idea
Analyst (render_idea, render_revision, render_query_task); batches of papers for the
relevance screener (render_screening_batch); the closest papers with their readings for
the Novelty Investigator (render_novelty_task); the analysis's claims and verdict for the
Skeptic (render_skeptic_queries, render_skeptic_challenges); the challenges for the
Novelty Investigator's revision (render_novelty_revision).

Agents never read the database themselves. The orchestrator gives each one a curated
packet, rendered as clearly labelled blocks so the model knows what it may cite:

    <paper id="..." year="2024" venue="ACL">
    <title>...</title>
    <authors>...</authors>
    <note>Only the abstract is available ...</note>
    <abstract>...</abstract>
    <chunk id="c_1" section="Limitations">...</chunk>
    </paper>

Paper text can't break out of its block: if it contains our tag names, they are
neutralized, so a malicious "</paper> new instructions" stays inside the data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ABSTRACT_ONLY_NOTE = (
    "Only the abstract of this paper is available. Analyse the abstract only, and never imply "
    "that you read the full paper."
)
FULL_TEXT_NOTE = "Passages from the full text are included as chunks, each with its ID and section."

_TAGS = re.compile(r"<(/?)(paper|papers|title|authors|note|abstract|chunk|reading|coverage|claims|claim|verdict|examined|challenges|challenge|complexity|constraints|field_brief|subtopic|items|item|pairs|pair|finding|setting|statement|statements|quote|material)\b",
                   re.IGNORECASE)
_QUOTE_IN_ATTRIBUTE = re.compile(r'["<>]')


@dataclass(frozen=True)
class ChunkForReading:
    chunk_id: str
    section_title: str | None
    text: str


@dataclass(frozen=True)
class PaperForReading:
    """The parts of a stored paper the Paper Reader is shown."""

    paper_id: str
    title: str
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    authors: tuple[str, ...] = ()
    chunks: tuple[ChunkForReading, ...] = field(default_factory=tuple)

    @property
    def source_level(self) -> str:
        """'full_text' if passages are included, otherwise 'abstract'."""
        return "full_text" if self.chunks else "abstract"


def _contain(text: str) -> str:
    """Neutralizes our tag names inside paper text ('</paper' -> '&lt;/paper')."""
    return _TAGS.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", text)


def _attribute(value: str) -> str:
    return _QUOTE_IN_ATTRIBUTE.sub("", value)


def render_paper(paper: PaperForReading, *, max_authors: int = 10) -> str:
    """The Paper Reader's input for one paper. Raises ValueError if there's no text to read."""
    if not paper.abstract and not paper.chunks:
        raise ValueError(f"Paper {paper.paper_id} has neither an abstract nor full-text chunks to read.")

    attributes = f'id="{_attribute(paper.paper_id)}"'
    if paper.year is not None:
        attributes += f' year="{paper.year}"'
    if paper.venue:
        attributes += f' venue="{_attribute(paper.venue)}"'

    lines = [f"<paper {attributes}>", f"<title>{_contain(paper.title)}</title>"]
    if paper.authors:
        shown = list(paper.authors[:max_authors])
        if len(paper.authors) > max_authors:
            shown.append(f"and {len(paper.authors) - max_authors} more")
        lines.append(f"<authors>{_contain(', '.join(shown))}</authors>")
    lines.append(f"<note>{FULL_TEXT_NOTE if paper.chunks else ABSTRACT_ONLY_NOTE}</note>")
    if paper.abstract:
        lines.append(f"<abstract>\n{_contain(paper.abstract)}\n</abstract>")
    for chunk in paper.chunks:
        section = f' section="{_attribute(chunk.section_title)}"' if chunk.section_title else ""
        lines.append(f'<chunk id="{_attribute(chunk.chunk_id)}"{section}>\n{_contain(chunk.text)}\n</chunk>')
    lines.append("</paper>")
    return "\n".join(lines)


# --- the Idea Analyst's packet ---------------------------------------------------------------------------
#
#     <idea>the user's text</idea>
#     <field>NLP</field>
#     <seed_papers>
#     - 2401.12345
#     </seed_papers>
#     <complexity>beginner: ...</complexity>
#     <constraints>
#     - available time: 8 weeks
#     </constraints>

_IDEA_TAGS = re.compile(r"<(/?)(idea|field|seed_papers|complexity|constraints|field_request|interests|technologies|exclusions|domain|count)\b",
                        re.IGNORECASE)

COMPLEXITY_NOTES = {
    "beginner": "beginner: a well-scoped empirical question; existing models and public datasets used as-is",
    "intermediate": "intermediate: comparisons or adaptations with independent design choices and several baselines",
    "advanced": "advanced: a new method, problem formulation or substantial system",
}


@dataclass(frozen=True)
class IdeaForAnalysis:
    """What the Idea Analyst is shown for a Mode 1 request (doc 02 §2.1)."""

    idea: str
    complexity: str
    field: str | None = None
    seed_papers: tuple[str, ...] = ()
    constraints: tuple[tuple[str, str], ...] = ()
    """(name, value) pairs from the constraints profile, e.g. ('available time', '8 weeks')."""


def _contain_user_text(text: str) -> str:
    return _IDEA_TAGS.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", text.strip())


def render_idea(request: IdeaForAnalysis) -> str:
    """The Idea Analyst's input. Raises ValueError for an empty idea."""
    if not request.idea.strip():
        raise ValueError("The idea is empty.")
    lines = [f"<idea>\n{_contain_user_text(request.idea)}\n</idea>"]
    if request.field:
        lines.append(f"<field>{_contain_user_text(request.field)}</field>")
    if request.seed_papers:
        seeds = "\n".join(f"- {_contain_user_text(seed)}" for seed in request.seed_papers)
        lines.append(f"<seed_papers>\n{seeds}\n</seed_papers>")
    lines.append(f"<complexity>{COMPLEXITY_NOTES.get(request.complexity, request.complexity)}</complexity>")
    if request.constraints:
        items = "\n".join(f"- {_contain_user_text(name)}: {_contain_user_text(value)}"
                          for name, value in request.constraints)
        lines.append(f"<constraints>\n{items}\n</constraints>")
    return "\n".join(lines)


# --- the Idea Analyst's revision packet ----------------------------------------------------------------
#
#     (the idea packet above)
#     <reading>{the earlier reading as JSON}</reading>
#     <answers>
#     <answer question="Which languages?">Georgian and Armenian</answer>
#     </answers>

_REVISION_TAGS = re.compile(r"<(/?)(reading|answers|answer)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QuestionAnswer:
    question: str
    answer: str


def _contain_revision_text(text: str) -> str:
    text = _contain_user_text(text)
    return _REVISION_TAGS.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", text)


def render_revision(request: IdeaForAnalysis, reading_json: str, answers: tuple[QuestionAnswer, ...]) -> str:
    """The Idea Analyst's input for revising its reading with the user's answers."""
    if not answers:
        raise ValueError("Nothing to revise: no answers.")
    items = "\n".join(
        f'<answer question="{_attribute(_contain_revision_text(item.question))}">'
        f"{_contain_revision_text(item.answer)}</answer>"
        for item in answers
    )
    return "\n".join([
        render_idea(request),
        f"<reading>\n{_contain_revision_text(reading_json)}\n</reading>",
        f"<answers>\n{items}\n</answers>",
    ])



# --- the Idea Analyst's query-planning packet ----------------------------------------------------------
#
#     <candidate>
#     <title>...</title>
#     <research_question>...</research_question>
#     <facet number="1" kind="problem" key="yes">...</facet>
#     </candidate>
#     <task>Write 15-20 queries. Quotas: ...</task>

_CANDIDATE_TAGS = re.compile(r"<(/?)(candidate|title|research_question|facet|task)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FacetForPlanning:
    number: int
    kind: str
    text: str
    is_key: bool


@dataclass(frozen=True)
class CandidateForPlanning:
    title: str
    research_question: str
    facets: tuple[FacetForPlanning, ...]


def _contain_candidate_text(text: str) -> str:
    return _CANDIDATE_TAGS.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", text.strip())


def render_query_task(candidate: CandidateForPlanning, *, task: str) -> str:
    """The Idea Analyst's input for a query plan. `task` states the count and quotas (written by code)."""
    if not candidate.facets:
        raise ValueError("A candidate needs facets to plan queries.")
    lines = ["<candidate>", f"<title>{_contain_candidate_text(candidate.title)}</title>",
             f"<research_question>{_contain_candidate_text(candidate.research_question)}</research_question>"]
    for facet in candidate.facets:
        key = "yes" if facet.is_key else "no"
        lines.append(f'<facet number="{facet.number}" kind="{facet.kind}" key="{key}">'
                     f"{_contain_candidate_text(facet.text)}</facet>")
    lines.append("</candidate>")
    lines.append(f"<task>{task}</task>")
    return "\n".join(lines)



# --- the relevance screener's packet -------------------------------------------------------------------
#
#     (the candidate block of render_query_task, without a task)
#     <papers>
#     <paper label="P1" year="2024"><title>...</title><abstract>...</abstract></paper>
#     </papers>

SCREENING_ABSTRACT_CHARS = 1500  # enough to judge relevance; keeps a batch of 25 near 10k tokens


@dataclass(frozen=True)
class PaperForScreening:
    label: str
    title: str
    year: int | None = None
    abstract: str | None = None


def _shorten_abstract(text: str, limit: int = SCREENING_ABSTRACT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " [...]"


def render_screening_batch(candidate: CandidateForPlanning, papers: tuple[PaperForScreening, ...]) -> str:
    """The relevance screener's input: the idea, then the batch of papers."""
    if not papers:
        raise ValueError("A screening batch needs papers.")
    return "\n".join([render_query_task(candidate, task="Score every paper in the batch below."),
                      _screening_papers(papers)])


def _screening_papers(papers: tuple[PaperForScreening, ...]) -> str:
    lines = ["<papers>"]
    for paper in papers:
        year = f' year="{paper.year}"' if paper.year else ""
        abstract = (f"<abstract>{_contain(_shorten_abstract(paper.abstract))}</abstract>" if paper.abstract
                    else "<abstract>(no abstract available)</abstract>")
        lines.append(f'<paper label="{_attribute(paper.label)}"{year}><title>{_contain(paper.title)}</title>'
                     f"{abstract}</paper>")
    lines.append("</papers>")
    return "\n".join(lines)


# --- the Novelty Investigator's packet ------------------------------------------------------------------
#
#     (the candidate block)
#     <papers>
#     <paper label="P1" year="2026" type="preprint" text="abstract only">
#     <title>...</title>
#     <abstract>...</abstract>
#     <reading>contribution: ...; methods: ...; findings: ...</reading>
#     </paper>
#     </papers>
#     <coverage>614 papers found, 614 screened, 24 read ...</coverage>

_READING_FIELDS = (("setting", "setting"), ("tasks", "tasks"), ("methods", "methods"), ("datasets", "datasets"),
                   ("metrics", "metrics"), ("baselines", "baselines"))


@dataclass(frozen=True)
class PaperForNovelty:
    label: str
    title: str
    tier: int
    year: int | None = None
    abstract: str | None = None
    chunks: tuple[ChunkForReading, ...] = ()
    reading: dict | None = None
    """The stored Paper Reader analysis content (doc 10 §5.3), if any."""
    is_new: bool = False
    """Found by the Skeptic and not yet in the facet matrix (the revision fills its cells)."""


def summarize_reading(content: dict | None) -> str:
    """A compact, quote-free summary of a stored analysis: what the paper does and finds."""
    if not content:
        return "(no reading available)"
    parts = [f"contribution: {content.get('contribution_type', 'unknown')}"]
    for label, field in _READING_FIELDS:
        value = content.get(field)
        if isinstance(value, list) and value:
            parts.append(f"{label}: {', '.join(value)}")
        elif isinstance(value, str) and value:
            parts.append(f"{label}: {value}")
    for item in content.get("findings", [])[:4]:
        parts.append(f"finding ({item.get('direction', '?')}): {item.get('claim', '')}")
    for item in content.get("limitations", [])[:3]:
        parts.append(f"admitted limitation: {item.get('statement', '')}")
    return "; ".join(parts)


def render_novelty_task(candidate: CandidateForPlanning, papers: tuple[PaperForNovelty, ...], *, coverage: str) -> str:
    """The Novelty Investigator's input: the idea, the closest papers, and the search coverage."""
    if not papers:
        raise ValueError("The Novelty Investigator needs at least one paper.")
    lines = [render_query_task(candidate, task="Compare the idea with every paper below, facet by facet."), "<papers>"]
    for paper in papers:
        year = f' year="{paper.year}"' if paper.year else ""
        kind = "peer-reviewed" if paper.tier == 1 else "preprint"
        level = "full text" if paper.chunks else "abstract only"
        new = ' new="yes"' if paper.is_new else ""
        lines.append(f'<paper label="{_attribute(paper.label)}"{year} type="{kind}" text="{level}"{new}>')
        lines.append(f"<title>{_contain(paper.title)}</title>")
        if paper.abstract:
            lines.append(f"<abstract>\n{_contain(paper.abstract)}\n</abstract>")
        for chunk in paper.chunks:
            section = f' section="{_attribute(chunk.section_title)}"' if chunk.section_title else ""
            lines.append(f'<chunk id="{_attribute(chunk.chunk_id)}"{section}>\n{_contain(chunk.text)}\n</chunk>')
        lines.append(f"<reading>{_contain(summarize_reading(paper.reading))}</reading>")
        lines.append("</paper>")
    lines.append("</papers>")
    lines.append(f"<coverage>{_contain(coverage)}</coverage>")
    return "\n".join(lines)



# --- the Skeptic's packets ------------------------------------------------------------------------------
#
#     (the candidate block)
#     <claims>
#     <claim label="C1" kind="gap" status="verified">...</claim>
#     </claims>
#     <verdict>Potential opportunity (no verified match; the search has limits)</verdict>
#     <examined>
#     - Title of an examined paper (2024)
#     </examined>                                  (query planning)
#     <papers>...</papers>                         (challenges: the new papers, as for A4)


@dataclass(frozen=True)
class ClaimForSkeptic:
    label: str
    kind: str
    text: str
    status: str


def _skeptic_context(candidate: CandidateForPlanning, claims: tuple[ClaimForSkeptic, ...], verdict: str,
                     task: str) -> list[str]:
    lines = [render_query_task(candidate, task=task), "<claims>"]
    for claim in claims:
        lines.append(f'<claim label="{_attribute(claim.label)}" kind="{claim.kind}" status="{claim.status}">'
                     f"{_contain(claim.text)}</claim>")
    lines.append("</claims>")
    lines.append(f"<verdict>{_contain(verdict)}</verdict>")
    return lines


def render_skeptic_queries(candidate: CandidateForPlanning, claims: tuple[ClaimForSkeptic, ...], verdict: str,
                           examined: tuple[str, ...]) -> str:
    """The Skeptic's input for planning its searches."""
    lines = _skeptic_context(candidate, claims, verdict, "Plan searches that could disprove the idea's novelty.")
    lines.append("<examined>")
    lines.extend(f"- {_contain(title)}" for title in examined)
    lines.append("</examined>")
    return "\n".join(lines)


def render_skeptic_challenges(candidate: CandidateForPlanning, claims: tuple[ClaimForSkeptic, ...], verdict: str,
                              papers: tuple[PaperForNovelty, ...]) -> str:
    """The Skeptic's input for writing challenges from the new papers it found."""
    lines = _skeptic_context(candidate, claims, verdict, "Challenge the analysis using the new papers below.")
    novelty = render_novelty_task(candidate, papers, coverage="")
    lines.append(novelty[novelty.index("<papers>"):novelty.index("</papers>") + len("</papers>")])
    return "\n".join(lines)



# --- the Novelty Investigator's revision packet -----------------------------------------------------------
#
#     (the candidate block and the claims, as for the Skeptic)
#     <challenges>
#     <challenge label="K1" type="prior_work_found" target="C2" round="1">
#     argument ...
#     quote from P14: "..."
#     </challenge>
#     </challenges>
#     <papers>... (examined papers, then the Skeptic's with new="yes") ...</papers>


@dataclass(frozen=True)
class ChallengeForRevision:
    label: str
    type: str
    target: str | None
    round: int
    argument: str
    evidence: tuple[tuple[str, str], ...] = ()
    """(paper label, quote text)."""


def render_novelty_revision(candidate: CandidateForPlanning, claims: tuple[ClaimForSkeptic, ...],
                            challenges: tuple[ChallengeForRevision, ...],
                            papers: tuple[PaperForNovelty, ...]) -> str:
    """The Novelty Investigator's input for answering a Skeptic round."""
    if not challenges and not any(paper.is_new for paper in papers):
        raise ValueError("Nothing to revise: no challenges and no new papers.")
    lines = [render_query_task(candidate, task="Answer the challenges and add the new papers to the matrix."),
             "<claims>"]
    for claim in claims:
        lines.append(f'<claim label="{_attribute(claim.label)}" kind="{claim.kind}" status="{claim.status}">'
                     f"{_contain(claim.text)}</claim>")
    lines.append("</claims>")
    lines.append("<challenges>")
    for item in challenges:
        target = f' target="{_attribute(item.target)}"' if item.target else ""
        lines.append(f'<challenge label="{_attribute(item.label)}" type="{item.type}"{target} round="{item.round}">')
        lines.append(_contain(item.argument))
        for paper, quote in item.evidence:
            lines.append(f'quote from {_attribute(paper)}: "{_contain(quote)}"')
        lines.append("</challenge>")
    lines.append("</challenges>")
    if papers:
        novelty = render_novelty_task(candidate, papers, coverage="")
        lines.append(novelty[novelty.index("<papers>"):novelty.index("</papers>") + len("</papers>")])
    return "\n".join(lines)


# --- the Value & Feasibility Evaluator's packet -------------------------------------------------------------
#
#     (the candidate block)
#     <complexity>beginner: ...</complexity>
#     <constraints>
#     - available time: 10 weeks
#     </constraints>
#     <claims>...</claims>
#     <challenges>
#     <challenge type="feasibility_risk" resolution="accepted">argument</challenge>
#     </challenges>
#     <papers>...</papers>


@dataclass(frozen=True)
class ChallengeSummary:
    type: str
    argument: str
    resolution: str


def render_value_feasibility(candidate: CandidateForPlanning, *, complexity: str,
                             constraints: tuple[tuple[str, str], ...], claims: tuple[ClaimForSkeptic, ...],
                             challenges: tuple[ChallengeSummary, ...], papers: tuple[PaperForNovelty, ...]) -> str:
    """The Value & Feasibility Evaluator's input."""
    lines = [render_query_task(candidate, task="Judge significance and feasibility for this user.")]
    lines.append(f"<complexity>{_contain(COMPLEXITY_NOTES.get(complexity, complexity))}</complexity>")
    lines.append("<constraints>")
    lines.extend(f"- {_contain(_contain_user_text(name))}: {_contain(_contain_user_text(value))}"
                 for name, value in constraints)
    if not constraints:
        lines.append("(none given)")
    lines.append("</constraints>")
    lines.append("<claims>")
    for claim in claims:
        lines.append(f'<claim label="{_attribute(claim.label)}" kind="{claim.kind}" status="{claim.status}">'
                     f"{_contain(claim.text)}</claim>")
    lines.append("</claims>")
    lines.append("<challenges>")
    for challenge in challenges:
        lines.append(f'<challenge type="{challenge.type}" resolution="{challenge.resolution}">'
                     f"{_contain(challenge.argument)}</challenge>")
    lines.append("</challenges>")
    if papers:
        novelty = render_novelty_task(candidate, papers, coverage="")
        lines.append(novelty[novelty.index("<papers>"):novelty.index("</papers>") + len("</papers>")])
    return "\n".join(lines)



# --- paper labels in stored text ----------------------------------------------------------------------------
#
# Agents refer to papers by labels (P1, P2, ...) that mean something only inside one call. Text
# that is stored (claims, closest works, uncertainties, challenges, rationales, assessments)
# gets the labels replaced by the paper's short title and year first, so a reader of the
# report never sees "P35" (step 4.9 live run).

_PAPER_LABEL = re.compile(r"\bP(\d{1,3})\b")
LABEL_TITLE_CHARS = 60


def name_papers(value, papers: dict):
    """`value` (a string, or lists/dicts of strings) with every known paper label replaced by
    '"Short title" (year)'. `papers` maps labels to objects with .title and .year."""
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            paper = papers.get(match.group(0))
            if paper is None:
                return match.group(0)
            title = paper.title if len(paper.title) <= LABEL_TITLE_CHARS else paper.title[:LABEL_TITLE_CHARS - 1] + "…"
            return f'"{title}" ({paper.year or "n.d."})'
        return _PAPER_LABEL.sub(replace, value)
    if isinstance(value, list):
        return [name_papers(item, papers) for item in value]
    if isinstance(value, dict):
        return {key: name_papers(item, papers) for key, item in value.items()}
    return value



# --- the Idea Analyst's field-brief packets (Mode 2, step 5.1) -------------------------------------------
#
#     <field_request>
#     <field>NLP</field>
#     <interests>evaluation of LLM agents</interests>
#     <technologies>PyTorch</technologies>
#     <exclusions>medical</exclusions>
#     <domain>education</domain>
#     <complexity>beginner: ...</complexity>
#     <constraints>
#     - available time: 10 weeks
#     </constraints>
#     <count>3</count>
#     </field_request>


@dataclass(frozen=True)
class FieldRequest:
    """What the Idea Analyst is shown for a Mode 2 request (doc 02 §2.2)."""

    field: str
    complexity: str
    interests: str | None = None
    technologies: str | None = None
    exclusions: tuple[str, ...] = ()
    domain: str | None = None
    constraints: tuple[tuple[str, str], ...] = ()
    count: int = 3


def render_field_request(request: FieldRequest) -> str:
    """The Idea Analyst's input for a field brief. Raises ValueError for an empty field."""
    if not request.field.strip():
        raise ValueError("The field is empty.")
    lines = ["<field_request>", f"<field>{_contain_user_text(request.field)}</field>"]
    for tag, value in (("interests", request.interests), ("technologies", request.technologies),
                       ("domain", request.domain)):
        if value:
            lines.append(f"<{tag}>{_contain_user_text(value)}</{tag}>")
    if request.exclusions:
        lines.append(f"<exclusions>{_contain_user_text('; '.join(request.exclusions))}</exclusions>")
    lines.append(f"<complexity>{COMPLEXITY_NOTES.get(request.complexity, request.complexity)}</complexity>")
    if request.constraints:
        items = "\n".join(f"- {_contain_user_text(name)}: {_contain_user_text(value)}"
                          for name, value in request.constraints)
        lines.append(f"<constraints>\n{items}\n</constraints>")
    lines.append(f"<count>{request.count}</count>")
    lines.append("</field_request>")
    return "\n".join(lines)


def render_field_revision(request: FieldRequest, brief_json: str, answers: tuple[QuestionAnswer, ...]) -> str:
    """The Idea Analyst's input for revising its field brief with the user's answers."""
    if not answers:
        raise ValueError("Nothing to revise: no answers.")
    items = "\n".join(
        f'<answer question="{_attribute(_contain_revision_text(item.question))}">'
        f"{_contain_revision_text(item.answer)}</answer>"
        for item in answers
    )
    return "\n".join([render_field_request(request), f"<reading>\n{_contain_revision_text(brief_json)}\n</reading>",
                      f"<answers>\n{items}\n</answers>"])



# --- the landscape screening packet (Mode 2, step 5.2a) ------------------------------------------------------
#
#     <field_brief>
#     <subtopic number="1">name: why it fits the user</subtopic>
#     </field_brief>
#     <papers>...</papers>                     (as for the candidate screening)


def render_landscape_screening(subtopics: tuple[tuple[int, str, str], ...],
                               papers: tuple[PaperForScreening, ...]) -> str:
    """The relevance screener's input in discovery mode: the field brief instead of a candidate.
    subtopics: (number, name, why it fits the user)."""
    if not papers:
        raise ValueError("A screening batch needs papers.")
    lines = ["<field_brief>"]
    lines.extend(f'<subtopic number="{number}">{_contain(name)}: {_contain(why)}</subtopic>'
                 for number, name, why in subtopics)
    lines.append("</field_brief>")
    lines.append(_screening_papers(papers))
    return "\n".join(lines)


# --- the opportunity miner's packets (Mode 2, step 5.2b) ---------------------------------------------------
#
#     <field_brief><subtopic number="1">name</subtopic>...</field_brief>
#     <items>
#     <item label="M1" kind="finding" direction="negative" paper="3">
#       <setting>...</setting><statement>...</statement><quote>...</quote></item>
#     </items>
#
#     <pairs>
#     <pair label="X1"><finding paper="3" direction="negative">(setting, statement, quote)</finding>
#                      <finding paper="7" direction="positive">...</finding></pair>
#     </pairs>


@dataclass(frozen=True)
class StatementForMining:
    label: str
    kind: str  # future_work | finding
    paper_number: int
    statement: str
    quote: str
    direction: str | None = None  # findings only
    setting: str | None = None  # findings only


def _statement_body(item: StatementForMining) -> str:
    setting = f"<setting>{_contain(item.setting)}</setting>" if item.setting else ""
    return f"{setting}<statement>{_contain(item.statement)}</statement><quote>{_contain(item.quote)}</quote>"


def render_mining_items(subtopic_names: tuple[str, ...], items: tuple[StatementForMining, ...]) -> str:
    """The opportunity miner's grouping input: the subtopics, then the statements to group."""
    if not items:
        raise ValueError("Grouping needs statements.")
    lines = ["<field_brief>"]
    lines.extend(f'<subtopic number="{n}">{_contain(name)}</subtopic>' for n, name in enumerate(subtopic_names, 1))
    lines += ["</field_brief>", "<items>"]
    for item in items:
        direction = f' direction="{_attribute(item.direction)}"' if item.direction else ""
        lines.append(f'<item label="{_attribute(item.label)}" kind="{_attribute(item.kind)}"{direction} '
                     f'paper="{item.paper_number}">{_statement_body(item)}</item>')
    lines.append("</items>")
    return "\n".join(lines)


def render_contradiction_pairs(pairs: tuple[tuple[str, StatementForMining, StatementForMining], ...]) -> str:
    """The contradiction check's input: (label, finding, finding) per pair."""
    if not pairs:
        raise ValueError("The contradiction check needs pairs.")
    lines = ["<pairs>"]
    for label, first, second in pairs:
        findings = "".join(f'<finding paper="{item.paper_number}" direction="{_attribute(item.direction or "")}">'
                           f"{_statement_body(item)}</finding>" for item in (first, second))
        lines.append(f'<pair label="{_attribute(label)}">{findings}</pair>')
    lines.append("</pairs>")
    return "\n".join(lines)


# --- the Scout's packet (Mode 2, task discover, step 5.3) --------------------------------------------------
#
#     <field_request>...</field_request>                     (as for the field brief)
#     <material>
#     <item label="O1" type="limitation_cluster" papers="5" author_groups="5" statements="Q1 Q4">label</item>
#     <item label="O8" type="subtopic" found="99" read="17" per_year="2024: 30, 2025: 41">name: why</item>
#     <item label="O20" type="dataset" papers="P3 P7">XNLI (tasks: ...)</item>
#     </material>
#     <statements>
#     <statement label="Q1" paper="P3" kind="finding" direction="negative">what it says<quote>...</quote></statement>
#     </statements>
#     <papers><paper label="P3" year="2026">title</paper></papers>


@dataclass(frozen=True)
class ItemForScout:
    label: str
    type: str
    text: str
    attributes: tuple[tuple[str, str], ...] = ()  # shown as XML attributes, in order


@dataclass(frozen=True)
class StatementForScout:
    label: str
    paper_label: str
    kind: str  # future_work | finding
    statement: str
    quote: str
    direction: str | None = None


@dataclass(frozen=True)
class PaperForScout:
    label: str
    title: str
    year: int | None


def render_scout_discover(request: FieldRequest, items: tuple[ItemForScout, ...],
                          statements: tuple[StatementForScout, ...], papers: tuple[PaperForScout, ...]) -> str:
    """The Scout's input for the discover task. Raises ValueError without statements to ground ideas in."""
    if not statements:
        raise ValueError("The Scout needs verified statements to ground its ideas in.")
    lines = [render_field_request(request), "<material>"]
    for item in items:
        attributes = "".join(f' {name}="{_attribute(value)}"' for name, value in item.attributes)
        lines.append(f'<item label="{_attribute(item.label)}" type="{_attribute(item.type)}"{attributes}>'
                     f"{_contain(item.text)}</item>")
    lines += ["</material>", "<statements>"]
    for s in statements:
        direction = f' direction="{_attribute(s.direction)}"' if s.direction else ""
        lines.append(f'<statement label="{_attribute(s.label)}" paper="{_attribute(s.paper_label)}" '
                     f'kind="{_attribute(s.kind)}"{direction}>{_contain(s.statement)}'
                     f"<quote>{_contain(s.quote)}</quote></statement>")
    lines += ["</statements>", "<papers>"]
    for paper in papers:
        year = f' year="{paper.year}"' if paper.year else ""
        lines.append(f'<paper label="{_attribute(paper.label)}"{year}>{_contain(paper.title)}</paper>')
    lines.append("</papers>")
    return "\n".join(lines)
