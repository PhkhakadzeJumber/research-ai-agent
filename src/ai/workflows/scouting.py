"""Generating candidates (Mode 2, stage `generating_candidates`, step 5.3; doc 04 A2, doc 10 §6.3-6.4).

scouting_step(sessions, investigation_id) runs the Scout's discover task once:
  1. code builds the Scout's input from the mined landscape (step 5.2b): the opportunity
     items (O1, O2, ...), every verified statement the miner saw (Q1, Q2, ...: future work
     and comparison findings, with their verified quote) and the papers read (P1, P2, ...);
  2. the Scout proposes 8-12 candidates (src/ai/agents/scout.py);
  3. code stores each one as an ordinary Candidate v1 (origin `discover`) with its facets, so
     Mode 1's evaluation pipeline can judge it unchanged, and one CandidateInspiration per
     known label (unknown labels are ignored). Paper labels in its texts become titles;
  4. the grounding rule (doc 04): a candidate needs inspirations that resolve to at least
     MIN_GROUNDING_QUOTES different verified quotes. Others are stored too, but screened out
     with the reason, so the rejection stays visible;
  5. the stage moves to `prescreening` (step 5.4: dedup, hard constraints, quick feasibility).

One Claude call. If it fails, the error is raised and nothing is stored, so the step can
simply be run again. Without any verified statement the Scout isn't called (NothingToGround).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.agents.scout import discover
from src.ai.context import ItemForScout, PaperForScout, StatementForScout, name_papers
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.field import field_request
from src.ai.workflows.landscape import confirmed_brief
from src.ai.workflows.mining import load_read_landscape, mining_statements
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status
from src.config import RoleLlmSettings
from src.db.models import Candidate, CandidateInspiration, Investigation, OpportunityItem
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.llm_calls import record_llm_calls

MIN_GROUNDING_QUOTES = 2  # doc 04: >=2 verified tier-1/2 quotes (every landscape paper is tier 1 or 2)
_ITEM_ORDER = ("limitation_cluster", "contradiction", "subtopic", "dataset")


class NothingToGround(ValueError):
    """The landscape has no verified statements, so no candidate could pass the grounding rule."""


@dataclass
class ScoutingOutcome:
    candidates: list[Candidate] = field(default_factory=list)
    screened_out: list[Candidate] = field(default_factory=list)
    """Failed the grounding rule (also in candidates)."""
    unknown_labels: int = 0
    items: int = 0
    statements: int = 0


def _item_views(items: list[OpportunityItem], q_labels: dict[uuid.UUID, list[str]],
                paper_labels: dict[uuid.UUID, str]) -> list[tuple[str, OpportunityItem, ItemForScout]]:
    views = []
    for item in sorted(items, key=lambda i: (_ITEM_ORDER.index(i.type) if i.type in _ITEM_ORDER else 9,
                                             -(i.counts or {}).get("papers", 0), i.label)):
        label = f"O{len(views) + 1}"
        counts, content = item.counts or {}, item.content or {}
        statements = " ".join(dict.fromkeys(q for quote_id in item.quote_ids for q in q_labels.get(quote_id, [])))
        if item.type == "limitation_cluster":
            attributes = (("papers", str(counts.get("papers", ""))), ("author_groups", str(counts.get("author_groups", ""))),
                          ("statements", statements))
            text = item.label
        elif item.type == "contradiction":
            attributes = (("statements", statements),)
            text = f"{item.label} (check: {content.get('check', '')})"
        elif item.type == "subtopic":
            per_year = ", ".join(f"{year}: {n}" for year, n in (counts.get("per_year") or {}).items())
            attributes = (("found", str(counts.get("landscape_papers", 0))), ("read", str(counts.get("read", 0))),
                          ("per_year", per_year))
            text = f"{item.label}: {content.get('why_it_fits_you', '')}"
        else:
            attributes = (("papers", " ".join(paper_labels[p] for p in item.paper_ids if p in paper_labels)),)
            tasks = ", ".join(content.get("tasks") or [])
            text = f"{item.label} (tasks: {tasks})" if tasks else item.label
        views.append((label, item, ItemForScout(label, item.type, text, attributes)))
    return views


async def scouting_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> ScoutingOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.GENERATING_CANDIDATES:
            raise StaleState(f"Investigation {investigation_id} isn't at generating_candidates.")
        request = field_request(investigation)
        brief = await confirmed_brief(session, investigation_id)
        loaded = await load_read_landscape(session, investigation_id, brief)
        items = list((await session.execute(select(OpportunityItem).where(
            OpportunityItem.investigation_id == investigation_id))).scalars())

    papers = {f"P{n}": paper for n, (paper, _) in enumerate(loaded.read, start=1)}
    paper_labels = {paper.id: label for label, paper in papers.items()}
    statements = mining_statements(loaded.read, loaded.quotes)
    q_views, q_quote, q_labels = [], {}, {}
    for n, statement in enumerate(statements, start=1):
        label, view = f"Q{n}", statement.view
        q_views.append(StatementForScout(label, f"P{view.paper_number}", view.kind, view.statement, view.quote,
                                         view.direction))
        q_quote[label] = statement.quote_id
        q_labels.setdefault(statement.quote_id, []).append(label)
    if not q_views:
        raise NothingToGround("The papers read have no verified future-work statements or findings to ground "
                              "ideas in.")
    item_views = _item_views(items, q_labels, paper_labels)
    o_item = {label: item for label, item, _ in item_views}
    outcome = ScoutingOutcome(items=len(item_views), statements=len(q_views))

    result = await discover(request, [view for _, _, view in item_views], q_views,
                            [PaperForScout(label, paper.title, paper.year) for label, paper in papers.items()],
                            settings=settings, model=model)

    async with sessions() as session:
        for proposed in result.output.candidates:
            links = []
            for inspiration in proposed.inspirations:
                note = name_papers(inspiration.note, papers)
                if inspiration.source in q_quote:
                    links.append(("quote", q_quote[inspiration.source], None, inspiration.role, note))
                elif inspiration.source in o_item:
                    links.append(("opportunity_item", None, o_item[inspiration.source].id, inspiration.role, note))
                else:
                    outcome.unknown_labels += 1
            candidate = await create_root_candidate(
                session, investigation_id=investigation_id, origin="discover",
                title=name_papers(proposed.title, papers),
                research_question=name_papers(proposed.research_question, papers),
                facets=[FacetToStore(f.kind, name_papers(f.text, papers), f.is_key, stated_by_user=False)
                        for f in proposed.facets],
            )
            candidate.origin_strategy = proposed.origin_strategy
            candidate.hypothesis = name_papers(proposed.hypothesis, papers)
            candidate.motivation = name_papers(proposed.motivation, papers)
            candidate.potential_contribution = name_papers(proposed.potential_contribution, papers)
            candidate.scout_notes = name_papers({**proposed.fit.model_dump(), "risks": proposed.risks}, papers)
            session.add_all(CandidateInspiration(candidate_id=candidate.id, kind=kind, quote_id=quote_id,
                                                 opportunity_item_id=item_id, role=role, note=note)
                            for kind, quote_id, item_id, role, note in links)
            await session.flush()
            outcome.candidates.append(candidate)
            grounding = len({quote_id for kind, quote_id, *_ in links if kind == "quote"})
            if grounding < MIN_GROUNDING_QUOTES:
                await set_candidate_status(
                    session, candidate.id, to=CandidateStatus.SCREENED_OUT, expected_from=CandidateStatus.PROPOSED,
                    reason=f"Grounding: {grounding} verified quote(s) behind it; at least {MIN_GROUNDING_QUOTES} "
                           "are needed.")
                outcome.screened_out.append(candidate)
        await record_llm_calls(session, result.records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.PRESCREENING,
                                    expected_from=Stage.GENERATING_CANDIDATES)
    return outcome
