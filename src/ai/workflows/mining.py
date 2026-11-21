"""Opportunity mining (Mode 2, stage `opportunity_mining`, step 5.2b; doc 06 §4, doc 10 §6.2).

mining_step(sessions, investigation_id) turns the landscape papers that were read (step 5.2a)
into OpportunityItems for the Scout. Everything is built from verified quotes only.

  limitation_cluster  Recurring open problems. The landscape was read from abstracts, which
                      almost never state limitations, so the material is future-work
                      statements plus negative and mixed findings (problems the papers
                      measured). The opportunity miner (a small model) groups them by the
                      problem they point at; code keeps a group only if its problem
                      statements come from at least MIN_PAPERS papers and MIN_AUTHOR_GROUPS
                      author groups (papers that share no author), so "recurring" is counted,
                      not asserted.
  contradiction       Within each group, code pairs a positive finding with a negative or
                      no-effect finding from another paper (at most MAX_PAIRS pairs); the
                      miner checks each pair, and only confirmed pairs are kept.
  subtopic            Pure code: each subtopic's landscape papers (from the searches that
                      found them), publications per year, the papers read and its surveys.
  dataset             Pure code: datasets the read papers name (names the reader's check found
                      in the text), with those papers' tasks.

The method x problem matrix (`combination`) waits for full text: from abstracts nearly every
method and task name occurs once, so the matrix would be empty cells.

At most two Claude calls. If one fails, the error is raised and nothing is stored, so the
step can simply be run again. On success the stage moves to `generating_candidates`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.agents.opportunity_miner import check_contradictions, group_statements
from src.ai.context import StatementForMining
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.landscape import confirmed_brief, is_survey, subtopic_attribution
from src.ai.workflows.states import Stage
from src.ai.workflows.transitions import StaleState, advance_investigation
from src.config import RoleLlmSettings
from src.db.models import Investigation, OpportunityItem, Paper, PaperAnalysis, Quote, RetrievalHit
from src.db.repositories.llm_calls import record_llm_calls

MIN_PAPERS = 3  # doc 06 §4: a recurring problem needs >=3 papers ...
MIN_AUTHOR_GROUPS = 2  # ... from >=2 distinct author groups
MAX_PAIRS = 15
PROBLEM_DIRECTIONS = ("negative", "mixed")
AGAINST_POSITIVE = ("negative", "no_effect")


@dataclass(frozen=True)
class Statement:
    view: StatementForMining
    paper_id: uuid.UUID
    quote_id: uuid.UUID

    @property
    def is_problem(self) -> bool:
        return self.view.kind == "future_work" or self.view.direction in PROBLEM_DIRECTIONS


@dataclass
class MiningOutcome:
    papers: int = 0
    statements: int = 0
    groups: int = 0
    clusters: list[OpportunityItem] = field(default_factory=list)
    dropped: list[tuple[str, int, int]] = field(default_factory=list)
    """Groups that aren't recurring enough: (label, papers, author groups)."""
    pairs_checked: int = 0
    contradictions: list[OpportunityItem] = field(default_factory=list)
    subtopics: list[OpportunityItem] = field(default_factory=list)
    datasets: list[OpportunityItem] = field(default_factory=list)


def author_groups(paper_ids, authors_by_paper: dict[uuid.UUID, list[str]]) -> int:
    """How many groups the papers form when papers sharing any author are joined."""
    papers = list(dict.fromkeys(paper_ids))
    parent = {paper: paper for paper in papers}

    def root(paper):
        while parent[paper] != paper:
            parent[paper] = parent[parent[paper]]
            paper = parent[paper]
        return paper

    first_with: dict[str, uuid.UUID] = {}
    for paper in papers:
        for name in authors_by_paper.get(paper, []):
            key = " ".join(name.lower().split())
            if not key:
                continue
            if key in first_with:
                parent[root(paper)] = root(first_with[key])
            else:
                first_with[key] = paper
    return len({root(paper) for paper in papers})


def _statements(read: list[tuple[Paper, PaperAnalysis]], quotes: dict[uuid.UUID, Quote]) -> list[Statement]:
    statements = []
    for number, (paper, analysis) in enumerate(read, start=1):
        material = [("future_work", item, None, None) for item in analysis.content.get("future_work", [])]
        material += [("finding", item, item.get("direction"), item.get("setting"))
                     for item in analysis.content.get("findings", []) if item.get("direction") != "not_applicable"]
        for kind, item, direction, setting in material:
            reference = item.get("quote") or {}
            quote_id = reference.get("quote_id")
            quote = quotes.get(uuid.UUID(quote_id)) if quote_id and reference.get("verified") else None
            if quote is None or not quote.verified:
                continue
            label = f"M{len(statements) + 1}"
            text = item.get("claim") if kind == "finding" else item.get("statement")
            statements.append(Statement(
                StatementForMining(label, kind, number, text or "", quote.text, direction, setting),
                paper.id, quote.id))
    return statements


def _subtopic_items(investigation_id, brief, landscape, read_ids, surveys) -> list[OpportunityItem]:
    """landscape: (paper id, year, subtopic names) of every landscape paper."""
    items = []
    for subtopic in brief.subtopics:
        members = [(paper_id, year) for paper_id, year, names in landscape if subtopic.name in names]
        per_year: dict[str, int] = {}
        for _, year in members:
            if year is not None:
                per_year[str(year)] = per_year.get(str(year), 0) + 1
        read = [paper_id for paper_id, _ in members if paper_id in read_ids]
        items.append(OpportunityItem(
            investigation_id=investigation_id, type="subtopic", label=subtopic.name, paper_ids=read,
            counts={"landscape_papers": len(members), "read": len(read), "per_year": dict(sorted(per_year.items()))},
            content={"why_it_fits_you": subtopic.why_it_fits_you,
                     "surveys": [str(paper_id) for paper_id in read if paper_id in surveys]}))
    return items


def _dataset_items(investigation_id, read: list[tuple[Paper, PaperAnalysis]]) -> list[OpportunityItem]:
    by_name: dict[str, tuple[str, list[uuid.UUID], set[str]]] = {}
    for paper, analysis in read:
        missing = {name.lower() for name in (analysis.content.get("verification") or {}).get("missing_names", [])}
        tasks = analysis.content.get("tasks", [])
        for name in analysis.content.get("datasets", []):
            key = " ".join(name.lower().split())
            if not key or key in missing:
                continue
            shown, papers, task_set = by_name.setdefault(key, (name, [], set()))
            if paper.id not in papers:
                papers.append(paper.id)
            task_set.update(tasks)
    return [OpportunityItem(investigation_id=investigation_id, type="dataset", label=shown, paper_ids=papers,
                            counts={"papers": len(papers)}, content={"tasks": sorted(task_set)})
            for shown, papers, task_set in sorted(by_name.values(), key=lambda v: (-len(v[1]), v[0].lower()))]


async def mining_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> MiningOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.OPPORTUNITY_MINING:
            raise StaleState(f"Investigation {investigation_id} isn't at opportunity_mining.")
        brief = await confirmed_brief(session, investigation_id)
        subtopics_of = await subtopic_attribution(session, investigation_id, brief)
        hits = (await session.execute(
            select(RetrievalHit, Paper).join(Paper, Paper.id == RetrievalHit.paper_id)
            .where(RetrievalHit.investigation_id == investigation_id, RetrievalHit.evaluation_id.is_(None))
            .order_by(RetrievalHit.source_rank.nulls_last(), Paper.title)
        )).all()
        landscape = [(paper.id, paper.year, subtopics_of(hit)) for hit, paper in hits]
        read: list[tuple[Paper, PaperAnalysis]] = []
        for hit, paper in hits:
            if not hit.selected_for_reading:
                continue
            analysis = (await session.execute(
                select(PaperAnalysis).where(PaperAnalysis.paper_id == paper.id)
                .order_by(PaperAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
            if analysis is not None:
                read.append((paper, analysis))
        quote_ids = {uuid.UUID(item["quote"]["quote_id"])
                     for _, analysis in read for name in ("future_work", "findings")
                     for item in analysis.content.get(name, []) if (item.get("quote") or {}).get("quote_id")}
        quotes = {quote.id: quote for quote in (await session.execute(
            select(Quote).where(Quote.id.in_(quote_ids)))).scalars()} if quote_ids else {}
    authors = {paper.id: list(paper.authors or []) for paper, _ in read}
    outcome = MiningOutcome(papers=len(read))
    statements = _statements(read, quotes)
    outcome.statements = len(statements)
    by_label = {statement.view.label: statement for statement in statements}
    records = []

    # recurring problems
    grouping = None
    if len(statements) >= 2:
        grouping = await group_statements(tuple(s.name for s in brief.subtopics),
                                          [s.view for s in statements], settings=settings, model=model)
        records += grouping.records
        outcome.groups = len(grouping.groups)
    pairs: list[tuple[str, Statement, Statement, str]] = []
    for label, members in (grouping.groups if grouping else []):
        group = [by_label[m] for m in members]
        problems = [s for s in group if s.is_problem]
        papers = list(dict.fromkeys(s.paper_id for s in problems))
        groups = author_groups(papers, authors)
        if len(papers) >= MIN_PAPERS and groups >= MIN_AUTHOR_GROUPS:
            outcome.clusters.append(OpportunityItem(
                investigation_id=investigation_id, type="limitation_cluster", label=label,
                quote_ids=[s.quote_id for s in group], paper_ids=list(dict.fromkeys(s.paper_id for s in group)),
                counts={"papers": len(papers), "author_groups": groups,
                        "future_work": sum(1 for s in problems if s.view.kind == "future_work"),
                        "findings": sum(1 for s in problems if s.view.kind == "finding")},
                content={"sources": [{"quote_id": str(s.quote_id), "paper_id": str(s.paper_id), "kind": s.view.kind,
                                      "direction": s.view.direction, "problem": s.is_problem} for s in group]},
                prompt_version=grouping.prompt_version))
        else:
            outcome.dropped.append((label, len(papers), groups))
        positives = [s for s in group if s.view.kind == "finding" and s.view.direction == "positive"]
        against = [s for s in group if s.view.kind == "finding" and s.view.direction in AGAINST_POSITIVE]
        for first in positives:
            for second in against:
                if first.paper_id != second.paper_id and len(pairs) < MAX_PAIRS:
                    pairs.append((f"X{len(pairs) + 1}", first, second, label))

    # contradictions
    if pairs:
        checks = await check_contradictions([(x, a.view, b.view) for x, a, b, _ in pairs],
                                            settings=settings, model=model)
        records += checks.records
        outcome.pairs_checked = len(pairs)
        for x, first, second, group_label in pairs:
            check = checks.checks.get(x)
            if check is not None and check.contradiction:
                outcome.contradictions.append(OpportunityItem(
                    investigation_id=investigation_id, type="contradiction", label=check.shared_question,
                    quote_ids=[first.quote_id, second.quote_id], paper_ids=[first.paper_id, second.paper_id],
                    counts={"papers": 2, "author_groups": author_groups([first.paper_id, second.paper_id], authors)},
                    content={"group": group_label, "directions": [first.view.direction, second.view.direction],
                             "check": check.reason},
                    prompt_version=checks.prompt_version))

    # code-only items
    read_ids = {paper.id for paper, _ in read}
    surveys = {paper.id for paper, _ in read if is_survey(paper.title)}
    outcome.subtopics = _subtopic_items(investigation_id, brief, landscape, read_ids, surveys)
    outcome.datasets = _dataset_items(investigation_id, read)

    async with sessions() as session:
        session.add_all([*outcome.clusters, *outcome.contradictions, *outcome.subtopics, *outcome.datasets])
        await record_llm_calls(session, records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.GENERATING_CANDIDATES,
                                    expected_from=Stage.OPPORTUNITY_MINING)
    return outcome
