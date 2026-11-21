"""Alternatives to the user's rejected idea (Mode 1, stage `generating_candidates`, step 5.5b; doc 05 §2).

alternatives_step(sessions, investigation_id), after the router saw the user's idea end rejected:
  1. code builds the Scout's packet from the evaluation of the idea's last evaluated version: why it
     was rejected (triage, verdicts), the closest works and the facet matrix, what blocked
     feasibility, the user's limits, and verified statements from the papers read for it (future
     work, and findings where something failed or was mixed);
  2. the Scout proposes 3-5 alternatives (src/ai/agents/scout.py alternatives());
  3. code stores them as candidates of their own (origin `alternative`, each linked to the user's
     idea by spawned_from_id) with the grounding rule of step 5.3, and the stage moves to
     `prescreening`, so they go through the same checks and light evaluation as Mode 2's ideas.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from src.ai.agents.scout import alternatives
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.mining import mining_statements
from src.ai.workflows.refining import build_request
from src.ai.workflows.scouting import ScoutingOutcome, at_generating, store_candidates
from src.ai.workflows.states import Stage
from src.ai.workflows.transitions import advance_investigation
from src.config import RoleLlmSettings
from src.db.models import Candidate, Evaluation, Paper, PaperAnalysis, Quote, RetrievalHit
from src.db.repositories.llm_calls import record_llm_calls

MAX_ALTERNATIVE_STATEMENTS = 40


class NoIdeaToReplace(ValueError):
    """The investigation has no rejected user idea with an evaluation."""


async def _evaluation_problem_quotes(session, evaluation_id: uuid.UUID) -> list[uuid.UUID]:
    """Verified future-work and negative/mixed-finding quotes of the papers read for one evaluation."""
    papers = list((await session.execute(
        select(RetrievalHit.paper_id).where(RetrievalHit.evaluation_id == evaluation_id,
                                            RetrievalHit.selected_for_reading.is_(True)))).scalars())
    read = []
    for paper_id in papers:
        analysis = (await session.execute(
            select(PaperAnalysis).where(PaperAnalysis.paper_id == paper_id)
            .order_by(PaperAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
        if analysis is not None:
            read.append((await session.get(Paper, paper_id), analysis))
    ids = {uuid.UUID(item["quote"]["quote_id"]) for _, a in read for name in ("future_work", "findings")
           for item in a.content.get(name, []) if (item.get("quote") or {}).get("quote_id")}
    quotes = {q.id: q for q in (await session.execute(select(Quote).where(Quote.id.in_(ids)))).scalars()} \
        if ids else {}
    return [s.quote_id for s in mining_statements(read, quotes) if s.is_problem]


async def alternatives_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> ScoutingOutcome:
    async with sessions() as session:
        investigation = await at_generating(session, investigation_id)
        root = (await session.execute(select(Candidate).where(
            Candidate.investigation_id == investigation_id, Candidate.origin == "user",
            Candidate.version == 1))).scalar_one_or_none()
        if root is None:
            raise NoIdeaToReplace(f"Investigation {investigation_id} has no idea of the user's.")
        evaluated = (await session.execute(
            select(Candidate).join(Evaluation, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.root_id == root.id).order_by(Candidate.version.desc()).limit(1))).scalar_one_or_none()
        if evaluated is None:
            raise NoIdeaToReplace("The user's idea has no evaluation to learn from.")
        evaluation = (await session.execute(
            select(Evaluation).where(Evaluation.candidate_id == evaluated.id)
            .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one()
        extra = await _evaluation_problem_quotes(session, evaluation.id)
        request, q_quote, papers = await build_request(session, investigation, evaluated, extra_quotes=extra,
                                                       max_statements=MAX_ALTERNATIVE_STATEMENTS)
    outcome = ScoutingOutcome(statements=len(request.statements))
    result = await alternatives(request, settings=settings, model=model)
    async with sessions() as session:
        await store_candidates(session, investigation_id, result.output.candidates, origin="alternative",
                               q_quote=q_quote, o_item={}, papers=papers, outcome=outcome, spawned_from_id=root.id)
        await record_llm_calls(session, result.records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.PRESCREENING,
                                    expected_from=Stage.GENERATING_CANDIDATES)
    return outcome
