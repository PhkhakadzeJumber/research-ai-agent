"""Refining candidates (stage `refining`, both workflows, step 5.5a; doc 05 §4 rules 2/4/5, §11.1).

refine_step(sessions, investigation_id) takes every candidate in `needs_refinement` (the router
already rejected lineages out of refinements, src/ai/workflows/routing.py) and, one at a time:
  1. builds the Scout's refine packet from what the evaluation found: the triage rule and
     reason, the verdicts, the closest works and the facet matrix, what blocks feasibility and
     the suggested simplifications, the user's limits, and verified statements (the parent's
     inspirations and the facet matrix's quotes);
  2. the Scout writes the next version (src/ai/agents/scout.py refine()), with a change note;
  3. code stores it as the next version of the lineage (origin `refine`, strategy `refinement`,
     parent and root linked), keeps the parent's inspirations and adds any new ones, and moves
     the parent to `refined` (final; it stays visible);
  4. the new version passes the pre-screen checks again (doc 05 §11.1): grounding (Scout ideas
     only: the user's own idea and its refinements have no inspirations to count) and the hard
     constraints in code. It goes to `light_evaluating`, or is screened out with the reason.
     Its light re-evaluation re-checks feasibility and novelty (doc 05 rule 5).
Then the stage moves to `light_evaluation`. Each candidate is stored as soon as it is done; if a
Scout call fails, the error is raised, and running the step again picks up the rest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.agents.scout import refine
from src.ai.context import PaperForScout, RefineRequest, name_papers
from src.ai.evidence.verdicts import LABELS
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.prescreening import hard_constraints
from src.ai.workflows.scouting import MIN_GROUNDING_QUOTES
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status
from src.config import RoleLlmSettings
from src.db.models import (
    Candidate,
    CandidateInspiration,
    Evaluation,
    FacetMatch,
    Investigation,
    Paper,
    Quote,
    TriageDecision,
    Verdict,
)
from src.db.repositories.candidates import FacetToStore, candidate_facets, create_refined_candidate
from src.db.repositories.evaluations import latest_evaluation
from src.db.repositories.llm_calls import record_llm_calls

MAX_CELLS = 40
MAX_STATEMENTS = 20


@dataclass
class Refinement:
    parent: Candidate
    child: Candidate
    passed: bool
    reason: str | None = None


@dataclass
class RefiningOutcome:
    refinements: list[Refinement] = field(default_factory=list)


async def _request(session, investigation: Investigation, parent: Candidate):
    """The Scout's packet, plus label -> quote id and label -> paper for mapping its answer."""
    facets = await candidate_facets(session, parent.id)
    facet_number = {f.id: f.position + 1 for f in facets}
    decision = (await session.execute(
        select(TriageDecision).where(TriageDecision.candidate_id == parent.id)
        .order_by(TriageDecision.created_at.desc()).limit(1))).scalar_one_or_none()
    evaluation = await latest_evaluation(session, parent.id)
    verdicts, works, matches, value = [], [], [], {}
    if evaluation is not None:
        rows = (await session.execute(select(Verdict).where(Verdict.evaluation_id == evaluation.id))).scalars()
        for v in sorted(rows, key=lambda v: ("novelty", "significance", "feasibility", "recommendation").index(
                v.dimension)):
            extra = [*v.uncertainties[:2], *v.blocking_requirements[:2]]
            verdicts.append(f"{v.dimension}: {LABELS.get(v.category, v.category)}"
                            + (f" ({'; '.join(extra)})" if extra else ""))
        works = list((evaluation.novelty_assessment or {}).get("closest_works", []))
        matches = list((await session.execute(
            select(FacetMatch).where(FacetMatch.evaluation_id == evaluation.id))).scalars())
        value = (evaluation.value_feasibility or {})

    # statements: the parent's inspirations, then the facet matrix's verified quotes
    quote_ids = list(dict.fromkeys([
        *(await session.execute(select(CandidateInspiration.quote_id).where(
            CandidateInspiration.candidate_id == parent.id, CandidateInspiration.quote_id.is_not(None)))).scalars(),
        *(m.quote_id for m in matches if m.quote_id)]))
    quotes = {q.id: q for q in (await session.execute(
        select(Quote).where(Quote.id.in_(quote_ids), Quote.verified.is_(True)))).scalars()} if quote_ids else {}
    quote_ids = [q for q in quote_ids if q in quotes][:MAX_STATEMENTS]

    paper_ids = list(dict.fromkeys([*(uuid.UUID(w["paper_id"]) for w in works), *(m.paper_id for m in matches),
                                    *(quotes[q].paper_id for q in quote_ids)]))
    papers_by_id = {p.id: p for p in (await session.execute(
        select(Paper).where(Paper.id.in_(paper_ids)))).scalars()} if paper_ids else {}
    p_label = {pid: f"P{n}" for n, pid in enumerate((p for p in paper_ids if p in papers_by_id), start=1)}
    q_label = {qid: f"Q{n}" for n, qid in enumerate(quote_ids, start=1)}

    feasibility = value.get("feasibility") or {}
    notes = parent.scout_notes or {}
    fit = (f"{notes.get('compute_tier', '?')}, about {notes.get('timeline_weeks', '?')} weeks, "
           f"data: {notes.get('data_source', 'not stated')}") if notes else "not stated"
    data = investigation.input or {}
    request = RefineRequest(
        complexity=investigation.complexity,
        constraints=tuple((str(k).replace("_", " "), str(v)) for k, v in (investigation.constraints_snapshot or {})
                          .items() if v not in (None, "", [])),
        exclusions=tuple(data.get("exclusions") or ()),
        version=parent.version, origin_strategy=parent.origin_strategy, title=parent.title,
        research_question=parent.research_question,
        facets=tuple((f.position + 1, f.kind, f.text, f.is_key) for f in facets),
        fit=fit, motivation=parent.motivation or (data.get("idea") or ""),
        contribution=parent.potential_contribution or "",
        triage_rule=decision.rule_number if decision else 0,
        triage_reason=decision.reason if decision else "refinement requested",
        verdicts=tuple(verdicts),
        closest_works=tuple((p_label[uuid.UUID(w["paper_id"])], bool(w.get("minor_difference_only")),
                             w.get("what_they_did", ""), w.get("how_the_idea_differs", ""))
                            for w in works if uuid.UUID(w["paper_id"]) in p_label),
        cells=tuple((p_label[m.paper_id], facet_number.get(m.facet_id, 0), m.overlap, q_label.get(m.quote_id))
                    for m in matches if m.paper_id in p_label)[:MAX_CELLS],
        blocking=tuple([*feasibility.get("blocking_requirements", []), *value.get("code_blocking", [])]),
        simplifications=tuple(feasibility.get("simplifications", [])),
        skill_gaps=tuple(feasibility.get("skill_gaps", [])),
        statements=tuple((q_label[q], p_label.get(quotes[q].paper_id, "P?"), quotes[q].text) for q in quote_ids),
        papers=tuple(PaperForScout(label, papers_by_id[pid].title, papers_by_id[pid].year)
                     for pid, label in p_label.items()),
    )
    papers = {label: papers_by_id[pid] for pid, label in p_label.items()}
    return request, {label: qid for qid, label in q_label.items()}, papers


async def refine_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> RefiningOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.REFINING:
            raise StaleState(f"Investigation {investigation_id} isn't at refining.")
        parents = list((await session.execute(
            select(Candidate).where(Candidate.investigation_id == investigation_id,
                                    Candidate.status == CandidateStatus.NEEDS_REFINEMENT)
            .order_by(Candidate.created_at))).scalars())
    outcome = RefiningOutcome()

    for parent in parents:
        async with sessions() as session:
            investigation = await session.get(Investigation, investigation_id)
            request, q_quote, papers = await _request(session, investigation, parent)
            root = await session.get(Candidate, parent.root_id)
        result = await refine(request, settings=settings, model=model)
        proposed = result.output.candidate

        async with sessions() as session:
            investigation = await session.get(Investigation, investigation_id)
            parent = await session.get(Candidate, parent.id)
            child = await create_refined_candidate(
                session, parent, title=name_papers(proposed.title, papers),
                research_question=name_papers(proposed.research_question, papers),
                facets=[FacetToStore(f.kind, name_papers(f.text, papers), f.is_key, stated_by_user=False)
                        for f in proposed.facets])
            child.origin_strategy = "refinement"
            child.hypothesis = name_papers(proposed.hypothesis, papers)
            child.motivation = name_papers(proposed.motivation, papers)
            child.potential_contribution = name_papers(proposed.potential_contribution, papers)
            child.scout_notes = name_papers({**proposed.fit.model_dump(), "risks": proposed.risks,
                                             "addresses": proposed.addresses}, papers)
            child.change_note = name_papers(proposed.change_note, papers)

            inherited = list((await session.execute(select(CandidateInspiration).where(
                CandidateInspiration.candidate_id == parent.id))).scalars())
            links = [(i.kind, i.quote_id, i.opportunity_item_id, i.role, i.note) for i in inherited]
            for inspiration in proposed.inspirations:
                quote_id = q_quote.get(inspiration.source)
                if quote_id and quote_id not in {link[1] for link in links}:
                    links.append(("quote", quote_id, None, inspiration.role, name_papers(inspiration.note, papers)))
            session.add_all(CandidateInspiration(candidate_id=child.id, kind=kind, quote_id=quote_id,
                                                 opportunity_item_id=item_id, role=role, note=note)
                            for kind, quote_id, item_id, role, note in links)
            await set_candidate_status(session, parent.id, to=CandidateStatus.REFINED,
                                       expected_from=CandidateStatus.NEEDS_REFINEMENT)

            # the pre-screen checks again
            checks: dict = {}
            reason = None
            if root is not None and root.origin != "user":
                grounding = len({quote_id for kind, quote_id, *_ in links if kind == "quote"})
                checks["grounding_quotes"] = grounding
                if grounding < MIN_GROUNDING_QUOTES:
                    reason = (f"Grounding: {grounding} verified quote(s) behind it; at least "
                              f"{MIN_GROUNDING_QUOTES} are needed.")
            hard_reason, hard = hard_constraints(child.scout_notes, investigation.constraints_snapshot or {},
                                                 investigation.complexity)
            checks.update(hard)
            reason = reason or hard_reason
            if reason:
                await set_candidate_status(session, child.id, to=CandidateStatus.SCREENED_OUT,
                                           expected_from=CandidateStatus.PROPOSED, reason=reason)
            else:
                await set_candidate_status(session, child.id, to=CandidateStatus.LIGHT_EVALUATING,
                                           expected_from=CandidateStatus.PROPOSED)
            child.prescreen = {"checks": checks, "decision": "screened_out" if reason else "light_evaluation",
                               "refinement_of": str(parent.id), "prompt_version": result.prompt_version}
            await record_llm_calls(session, result.records, investigation_id=investigation_id)
            outcome.refinements.append(Refinement(parent, child, passed=reason is None, reason=reason))

    async with sessions() as session:
        await advance_investigation(session, investigation_id, to=Stage.LIGHT_EVALUATION, expected_from=Stage.REFINING)
    return outcome
