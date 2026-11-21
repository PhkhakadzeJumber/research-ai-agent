"""The candidate loop shared by both commands (scripts/evaluate.py and scripts/discover.py), step 5.5a.

After triage, the router (src/ai/workflows/routing.py) picks the next stage; this module carries it
out and prints what happens, until the investigation reaches a stage that isn't built yet or ends:

    triage -> refining -> light_evaluation -> triage -> ...   (refine: the Scout writes the next version)
    triage -> generating_candidates -> prescreening -> light_evaluation -> triage -> ...
                                     (Mode 2 replenish: reserve first, then the Scout; Mode 1 alternatives)
    triage -> full_evaluation -> triage -> ...               (the best advanced candidates, step 5.6a)
    triage -> ranking -> reporting -> results_ready          (the ranked results; the report: scripts/report.py)

Exit codes as in the commands: 0 ok, 1 a step failed, 3 the budget is used up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.ai.workflows.budget import budget_left, cost_cap
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.alternatives import NoIdeaToReplace
from src.ai.workflows.full_evaluation import full_evaluation_step
from src.ai.workflows.generating import generate_step
from src.ai.workflows.light_evaluation import LightOutcome, light_evaluation_step, short_step_line
from src.ai.workflows.prescreening import PrescreenOutcome, prescreen_step
from src.ai.workflows.ranking import RankedResults, complete_results, ranked_results
from src.ai.workflows.refining import RefiningOutcome, refine_step
from src.ai.workflows.routing import route_after_triage
from src.ai.workflows.runner import EvaluationModels
from src.ai.workflows.scouting import NothingToGround, ScoutingOutcome
from src.ai.workflows.states import Mode, Stage
from src.db.models import Investigation
from src.llm import LlmError


@dataclass
class LoopModels:
    """Fakes for tests; None means the configured models."""

    evaluation: EvaluationModels | None = None
    scout_model: object = None
    search_factory: object = None
    quick_model: object = None


async def _over_budget(sessions: Sessions, investigation_id: uuid.UUID, before: str, command: str) -> bool:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if budget_left(investigation):
            return False
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped before "
              f"{before}. Resume with: python -m scripts.{command} --continue {investigation_id} --budget <more>")
        return True


# --- generating candidates and pre-screening -------------------------------------------------------------

def format_scouting(outcome: ScoutingOutcome) -> str:
    screened = {candidate.id for candidate in outcome.screened_out}
    source = f"From {outcome.items} mined items and {outcome.statements} statements" if outcome.items else \
        f"From {outcome.statements} statements"
    lines = [f"  {source}: {len(outcome.candidates)} candidates, {len(outcome.candidates) - len(screened)} grounded"
             + (f" | unknown labels ignored: {outcome.unknown_labels}" if outcome.unknown_labels else "")]
    for n, candidate in enumerate(outcome.candidates, start=1):
        notes = candidate.scout_notes or {}
        lines.append(f"\n  {n}. {candidate.title}  [{candidate.origin_strategy}; {notes.get('compute_tier')}, "
                     f"~{notes.get('timeline_weeks')} weeks]")
        lines.append(f"     RQ: {candidate.research_question}")
        if candidate.id in screened:
            lines.append(f"     SCREENED OUT: {candidate.rejection_reason}")
    return "\n".join(lines)


TASK_HEADERS = {
    "discover": "Generating candidate ideas (the Scout, one call)...",
    "replenish": "Replenishing: too few ideas survived...",
    "alternatives": "Looking for alternatives to your idea (the Scout, one call)...",
}


async def generate(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                   command: str) -> int:
    """Stage generating_candidates: discover, replenish or alternatives (src/ai/workflows/generating.py)."""
    models = models or LoopModels()
    if await _over_budget(sessions, investigation_id, "generating candidates", command):
        return 3
    print("\nGenerating candidates...")
    try:
        task, outcome = await generate_step(sessions, investigation_id, model=models.scout_model)
    except (NothingToGround, NoIdeaToReplace) as error:
        print(f"  {error} Nothing was generated.")
        return 1
    except LlmError as error:
        print(f"  The Scout failed: {error}. Nothing was stored; run --continue to try again.")
        return 1
    print(f"  {TASK_HEADERS[task]}")
    if outcome.from_reserve:
        print(f"  {outcome.from_reserve} idea(s) wait in reserve: no new ideas needed; pre-screening picks the "
              "next ones")
    else:
        print(format_scouting(outcome))
    return await prescreen(sessions, investigation_id, models, command=command)


def format_prescreen(outcome: PrescreenOutcome) -> str:
    lines = [f"  {outcome.considered} candidates: {len(outcome.light)} to light evaluation, "
             f"{len(outcome.reserve)} in reserve, {len(outcome.screened_out)} screened out"
             + (f" | quick feasibility reused for {outcome.reused_quick}" if outcome.reused_quick else "")]
    if not outcome.room:
        lines.append("  No light evaluations left under the cap (--max-light): the rest wait in reserve.")
    for candidate, reason in outcome.screened_out:
        lines.append(f"    out: {candidate.title}\n         {reason}")
    for n, candidate in enumerate(outcome.light, start=1):
        level = outcome.quick.get(candidate.id, {}).get("level", "?").replace("_", " ")
        lines.append(f"    {n}. {candidate.title}  [{candidate.origin_strategy}; quick feasibility: {level}]")
    for candidate in outcome.reserve:
        level = (outcome.quick.get(candidate.id, {}).get("level") or "?").replace("_", " ")
        lines.append(f"    reserve: {candidate.title}  [{candidate.origin_strategy}; {level}]")
    return "\n".join(lines)


async def prescreen(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                    command: str) -> int:
    models = models or LoopModels()
    if await _over_budget(sessions, investigation_id, "pre-screening", command):
        return 3
    print("\nPre-screening: duplicates, your constraints, a quick feasibility check...")
    try:
        outcome = await prescreen_step(sessions, investigation_id, model=models.quick_model)
    except LlmError as error:
        print(f"  The quick feasibility check failed: {error}. Nothing was stored; run --continue to try again.")
        return 1
    print(format_prescreen(outcome))
    return await evaluate_light(sessions, investigation_id, models=models.evaluation,
                                search_factory=models.search_factory, scout_model=models.scout_model,
                                command=command, quick_model=models.quick_model)


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
                         search_factory=None, scout_model=None, command: str = "discover", quick_model=None) -> int:
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
    return await route(sessions, investigation_id, LoopModels(models, scout_model, search_factory, quick_model),
                       command=command)


# --- full evaluation ------------------------------------------------------------------------------------

async def evaluate_full(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                        command: str) -> int:
    models = models or LoopModels()

    async def on_candidate(candidate, number, total) -> None:
        print(f"\nFull evaluation {number}/{total}: {candidate.title}"
              + (f" (v{candidate.version})" if candidate.version > 1 else "")
              + " (extends its light evaluation: earlier searches and readings are reused)")

    async def report(step, phase, outcome) -> None:
        line = short_step_line(step, outcome) if phase == "done" else None
        if line:
            print(f"  {line}")

    outcome = await full_evaluation_step(sessions, investigation_id, models=models.evaluation,
                                         search_factory=models.search_factory, report=report,
                                         on_candidate=on_candidate)
    if outcome.triaged:
        print("\nTriage after full evaluation:")
        for candidate, decision in outcome.triaged:
            print(f"  {candidate.title}\n    rule {decision.rule_number}: {decision.action} -> "
                  f"{candidate.status.replace('_', ' ')} ({decision.reason})")
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
    if outcome.stopped == "budget":
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped at "
              f"{outcome.stopped_at}. Resume with: python -m scripts.{command} --continue {investigation_id} "
              "--budget <more>")
        return 3
    if outcome.stopped:
        print(f"\nThe full evaluation stopped ({outcome.stopped}) at {outcome.stopped_at}. Run --continue to try again.")
        return 1
    return await route(sessions, investigation_id, models, command=command)


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
                                command=command, quick_model=models.quick_model)


# --- ranking (step 5.6b) ---------------------------------------------------------------------------------

def format_ranking(results: RankedResults) -> str:
    lines = ["\nRanked ideas:" if results.finalists else "\nNo idea became a finalist."]
    for n, r in enumerate(results.finalists, start=1):
        s = r.standing
        lines.append(f"  {n}. {r.candidate.title}")
        lines.append(f"     {s.recommendation}, novelty {s.novelty}, feasibility {s.feasibility}, "
                     f"significance {s.significance}".replace("_", " ")
                     + (f" | above #{n + 1}: {r.ahead_because}" if r.ahead_because else ""))
    if results.advanced:
        lines.append("Passed screening, not fully evaluated: " + "; ".join(r.candidate.title for r in results.advanced))
    lines.append(f"Other ideas set aside: {len(results.others)} (each with its reason in the report)")
    return "\n".join(lines)


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
                                    command=command, quick_model=models.quick_model)
    if stage == Stage.GENERATING_CANDIDATES:
        return await generate(sessions, investigation_id, models, command=command)
    if stage == Stage.PRESCREENING:
        return await prescreen(sessions, investigation_id, models, command=command)
    if stage == Stage.FULL_EVALUATION:
        return await evaluate_full(sessions, investigation_id, models, command=command)
    if stage == Stage.RANKING:
        async with sessions() as session:
            results = await ranked_results(session, investigation_id)
        await complete_results(sessions, investigation_id)
        if mode == Mode.DISCOVER:
            print(format_ranking(results))
        async with sessions() as session:
            cost = (await session.get(Investigation, investigation_id)).cost_usd
        print(f"\nDone. Total cost: ${cost:.2f}")
        print(f"Full report: python -m scripts.report {investigation_id} [--out report.md]")
        return 0
    print(f"Nothing to continue at stage {stage}.")
    return 0
