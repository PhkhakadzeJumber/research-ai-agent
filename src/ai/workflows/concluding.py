"""The end of an evaluation and of Workflow A (doc 05 §11.2, §11.4; doc 07 §4; step 4.9).

conclude_step(sessions, evaluation_id), evaluation step `concluding`, in this order
(doc 10 §7.1): freeze the coverage snapshot, then compute the verdicts and store them as
Verdict rows (novelty, significance, feasibility, recommendation; never changed
afterwards), then end the evaluation (`done`, or `partial` if a step was degraded).

finish_investigation(sessions, investigation_id), Mode 1 after its full evaluation: stage
`full_evaluation -> triage`, then the router (src/ai/workflows/routing.py, step 5.5a) picks the next
stage: `refining` for a refinement (carried out by scripts/candidate_loop.py), or `ranking ->
reporting -> results_ready` (wrap_up):
  - triage (doc 05 §4): the first matching rule becomes a stored TriageDecision, and the
    candidate's status follows it: finalize -> finalist, reject -> rejected (with the
    reason), refine -> needs_refinement, advance -> advanced; expand_search leaves it as it
    is. An expanded search and alternatives aren't carried out yet (recorded and reported);
  - ranking and reporting: Mode 1 has one candidate, so nothing to order; the report is
    built from what is stored, on demand (scripts/report.py);
  - results_ready: the investigation is `completed`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from src.ai.workflows.evaluation import Sessions, at_step
from src.ai.workflows.routing import Route, route_after_triage
from src.ai.workflows.states import CandidateStatus, EvaluationStep, Stage
from src.ai.workflows.transitions import advance_evaluation, advance_investigation, set_candidate_status
from src.ai.workflows.value_feasibility import Verdicts, verdicts_for
from src.db.models import Candidate, Evaluation, TriageDecision, Verdict
from src.db.repositories.evaluations import latest_evaluation

STATUS_FOR_ACTION = {
    "finalize": CandidateStatus.FINALIST,
    "reject": CandidateStatus.REJECTED,
    "refine": CandidateStatus.NEEDS_REFINEMENT,
    "advance": CandidateStatus.ADVANCED,
}
NOT_YET_EXECUTABLE = {
    "expand_search": "Expanding the search is carried out with step 5; the verdict stays flagged until then.",
}


def _uncertainties(name: str, verdicts: Verdicts, evaluation: Evaluation) -> list[str]:
    if name == "novelty":
        return [*verdicts.novelty.weaknesses, *(evaluation.novelty_assessment or {}).get("uncertainties", [])]
    if name == "recommendation":
        return (["needs further investigation: the evidence leaves important questions open"]
                if verdicts.recommendation.needs_further_investigation else [])
    return list(getattr(verdicts, name).weaknesses)


async def conclude_step(sessions: Sessions, evaluation_id: uuid.UUID) -> Verdicts:
    async with sessions() as session:
        evaluation = await at_step(session, evaluation_id, EvaluationStep.CONCLUDING)
        verdicts = await verdicts_for(session, evaluation_id)
        evaluation = await session.get(Evaluation, evaluation_id)
        evaluation.coverage = verdicts.coverage  # 1. frozen first (doc 10 §7.1)
        feasibility_blocking = [w for w in verdicts.feasibility.weaknesses if not w.startswith(("skill gap:",
                                                                                                 "unconfirmed data:"))]
        for name in ("novelty", "significance", "feasibility"):
            verdict = getattr(verdicts, name)
            session.add(Verdict(
                evaluation_id=evaluation_id, dimension=name, category=verdict.category, rule=verdict.rule,
                rule_trace=list(verdict.trace), uncertainties=_uncertainties(name, verdicts, evaluation),
                blocking_requirements=feasibility_blocking if name == "feasibility" else [],
                rules_version=verdict.rules_version))
        rec = verdicts.recommendation
        session.add(Verdict(evaluation_id=evaluation_id, dimension="recommendation", category=rec.category,
                            rule=rec.rule, rule_trace=list(rec.trace),
                            uncertainties=_uncertainties("recommendation", verdicts, evaluation),
                            rules_version=rec.rules_version))
        await session.flush()
        await advance_evaluation(session, evaluation_id, to=EvaluationStep.DONE,
                                 expected_from=EvaluationStep.CONCLUDING)
    return verdicts


@dataclass(frozen=True)
class FinishOutcome:
    decision: TriageDecision
    candidate_status: str
    not_executed: str | None
    """Why the decided action isn't carried out yet (expand_search)."""
    route: Route | None = None
    """Where the investigation went after triage (src/ai/workflows/routing.py)."""


async def stored_verdicts(session, evaluation_id: uuid.UUID) -> dict[str, Verdict]:
    rows = (await session.execute(select(Verdict).where(Verdict.evaluation_id == evaluation_id))).scalars()
    return {row.dimension: row for row in rows}


async def triage_candidate(session, candidate_id: uuid.UUID, evaluation_id: uuid.UUID, *,
                           expected_from: str) -> TriageDecision:
    """Doc 05 §4 for one concluded evaluation: the first matching rule becomes a stored TriageDecision
    and the candidate's status follows it (expand_search leaves it as it is). Both workflows use it."""
    verdicts = await verdicts_for(session, evaluation_id)
    rule = verdicts.triage
    stored = await stored_verdicts(session, evaluation_id)
    decision = TriageDecision(candidate_id=candidate_id, evaluation_id=evaluation_id, rule_number=rule.number,
                              action=rule.action, reason=rule.reason,
                              evidence={"verdict_ids": [str(v.id) for v in stored.values()]})
    session.add(decision)
    status = STATUS_FOR_ACTION.get(rule.action)
    if status is not None:
        await set_candidate_status(session, candidate_id, to=status, expected_from=expected_from,
                                   reason=f"Rule {rule.number}: {rule.reason}")
    await session.flush()
    return decision


async def finish_investigation(sessions: Sessions, investigation_id: uuid.UUID) -> FinishOutcome:
    async with sessions() as session:
        candidates = list((await session.execute(
            select(Candidate).where(Candidate.investigation_id == investigation_id,
                                    Candidate.status == CandidateStatus.FULL_EVALUATING)
        )).scalars())
        if len(candidates) != 1:
            raise ValueError(f"Expected one candidate in full evaluation, found {len(candidates)}.")
        candidate = candidates[0]
        evaluation = await latest_evaluation(session, candidate.id)
        if evaluation is None or evaluation.status not in ("done", "partial"):
            raise ValueError("The candidate's evaluation hasn't been concluded yet.")
        await advance_investigation(session, investigation_id, to=Stage.TRIAGE, expected_from=Stage.FULL_EVALUATION)
        decision = await triage_candidate(session, candidate.id, evaluation.id,
                                          expected_from=CandidateStatus.FULL_EVALUATING)
    route = await route_after_triage(sessions, investigation_id)
    if route.stage == Stage.RANKING:
        await wrap_up(sessions, investigation_id)
    async with sessions() as session:
        final_status = (await session.get(Candidate, candidate.id)).status
    return FinishOutcome(decision, final_status, NOT_YET_EXECUTABLE.get(decision.action), route)


async def wrap_up(sessions: Sessions, investigation_id: uuid.UUID) -> None:
    """From `ranking`, both workflows: the ranking is computed from the stored verdicts and the report is built
    on demand (src/ai/workflows/ranking.py, scripts/report.py), so ranking -> reporting -> results_ready (completed)."""
    async with sessions() as session:
        for current, target in ((Stage.RANKING, Stage.REPORTING), (Stage.REPORTING, Stage.RESULTS_READY)):
            await advance_investigation(session, investigation_id, to=target, expected_from=current)
