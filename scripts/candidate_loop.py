"""The candidate loop shared by both commands (scripts/evaluate.py and scripts/discover.py), step 5.5a.

After triage, the router (src/ai/workflows/routing.py) picks the next stage; this module carries it
out and prints what happens, until the investigation reaches a stage that isn't built yet or ends:

    triage -> refining -> light_evaluation -> triage -> ...   (refine: the Scout writes the next version)
    triage -> full_evaluation                                 (advanced candidates: step 5.6)
    triage -> ranking -> reporting -> results_ready          (Mode 1; Mode 2's ranking comes with 5.6)

Exit codes as in the commands: 0 ok, 1 a step failed, 3 the budget is used up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.ai.workflows.budget import budget_left, cost_cap
from src.ai.workflows.concluding import wrap_up
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.light_evaluation import LightOutcome, light_evaluation_step, short_step_line
from src.ai.workflows.refining import RefiningOutcome, refine_step
from src.ai.workflows.routing import route_after_triage
from src.ai.workflows.runner import EvaluationModels
from src.ai.workflows.states import Mode, Stage
from src.db.models import Investigation
from src.llm import LlmError


@dataclass
class LoopModels:
    """Fakes for tests; None means the configured models."""

    evaluation: EvaluationModels | None = None
    scout_model: object = None
    search_factory: object = None


async def _over_budget(sessions: Sessions, investigation_id: uuid.UUID, before: str, command: str) -> bool:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if budget_left(investigation):
            return False
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped before "
              f"{before}. Resume with: python -m scripts.{command} --continue {investigation_id} --budget <more>")
        return True


# --- light evaluation -----------------------------------------------------------------------------------

def format_light(outcome: LightOutcome) -> str:
    if not outcome.triaged:
        return ""
    lines = ["\nTriage after light evaluation:"]
    for candidate, decision in outcome.triaged:
        lines.append(f"  {candidate.title}\n    rule {decision.rule_number}: {decision.action} -> "
                     f"{candidate.status.replace('_', ' ')} ({decision.reason})")
    return "\n".join(lines)


async def evaluate_light(sessions: Sessions, investigation_id: uuid.UUID, *, models: EvaluationModels | None = None,
                         search_factory=None, scout_model=None, command: str = "discover") -> int:
    async def on_candidate(candidate, number, total) -> None:
        version = f" (v{candidate.version})" if candidate.version > 1 else ""
        print(f"\nLight evaluation {number}/{total}: {candidate.title}{version}")

    async def report(step, phase, outcome) -> None:
        line = short_step_line(step, outcome) if phase == "done" else None
        if line:
            print(f"  {line}")

    outcome = await light_evaluation_step(sessions, investigation_id, models=models, search_factory=search_factory,
                                          report=report, on_candidate=on_candidate)
    print(format_light(outcome))
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
    if outcome.stopped == "budget":
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped at "
              f"{outcome.stopped_at}. Resume with: python -m scripts.{command} --continue {investigation_id} "
              "--budget <more>")
        return 3
    if outcome.stopped:
        print(f"\nThe light evaluation stopped ({outcome.stopped}) at {outcome.stopped_at}. "
              f"Run --continue to try again.")
        return 1
    return await route(sessions, investigation_id, LoopModels(models, scout_model, search_factory), command=command)


# --- refining -------------------------------------------------------------------------------------------

def format_refining(outcome: RefiningOutcome) -> str:
    lines = []
    for r in outcome.refinements:
        lines.append(f"  v{r.parent.version} -> v{r.child.version}: {r.child.title}")
        lines.append(f"    change: {r.child.change_note}")
        lines.append("    -> light re-evaluation" if r.passed else f"    SCREENED OUT: {r.reason}")
    return "\n".join(lines)


async def refine(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels, *, command: str) -> int:
    if await _over_budget(sessions, investigation_id, "refining", command):
        return 3
    print("\nRefining (the Scout writes the next version of each idea sent back by triage)...")
    try:
        outcome = await refine_step(sessions, investigation_id, model=models.scout_model)
    except LlmError as error:
        print(f"  The Scout failed: {error}. Finished refinements are kept; run --continue to try again.")
        return 1
    print(format_refining(outcome))
    return await evaluate_light(sessions, investigation_id, models=models.evaluation,
                                search_factory=models.search_factory, scout_model=models.scout_model,
                                command=command)


# --- routing --------------------------------------------------------------------------------------------

async def route(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                command: str) -> int:
    """At stage `triage`: picks the next stage and carries it out as far as it is built."""
    models = models or LoopModels()
    decision = await route_after_triage(sessions, investigation_id)
    for candidate in decision.out_of_refinements:
        print(f"\n  rejected (no refinements left): {candidate.title}")
    print(f"\nNext: {decision.stage.replace('_', ' ')} ({decision.reason})")
    return await continue_at(sessions, investigation_id, models, command=command)


async def continue_at(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                      command: str) -> int:
    """Carries on from the loop's stages (triage, refining, light evaluation, ranking)."""
    models = models or LoopModels()
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        stage, mode = investigation.stage, investigation.mode
    if stage == Stage.TRIAGE:
        return await route(sessions, investigation_id, models, command=command)
    if stage == Stage.REFINING:
        return await refine(sessions, investigation_id, models, command=command)
    if stage == Stage.LIGHT_EVALUATION:
        return await evaluate_light(sessions, investigation_id, models=models.evaluation,
                                    search_factory=models.search_factory, scout_model=models.scout_model,
                                    command=command)
    if stage == Stage.FULL_EVALUATION:
        print("Full evaluation of the advanced candidates comes with step 5.6.")
        return 0
    if stage == Stage.RANKING and mode == Mode.EVALUATE:
        await wrap_up(sessions, investigation_id)
        async with sessions() as session:
            cost = (await session.get(Investigation, investigation_id)).cost_usd
        print(f"\nDone. Total cost: ${cost:.2f}")
        print(f"Full report: python -m scripts.report {investigation_id} [--out report.md]")
        return 0
    if stage == Stage.RANKING:
        print("Ranking the finalists and the Mode 2 report come with step 5.6.")
        return 0
    if stage == Stage.GENERATING_CANDIDATES:
        print("Replenishing and alternatives come with step 5.5b.")
        return 0
    print(f"Nothing to continue at stage {stage}.")
    return 0
