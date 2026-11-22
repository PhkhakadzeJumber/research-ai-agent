"""The candidate loop shared by both commands (scripts/evaluate.py and scripts/discover.py), step 5.5a; printing
moved onto the driver in step 9b.1.

The control flow lives in src/ai/workflows/driver.py (shared with the API); this module prints what it reports.
After triage, the router picks the next stage; the driver carries it out until the investigation ends:

    triage -> refining -> light_evaluation -> triage -> ...   (refine: the Scout writes the next version)
    triage -> generating_candidates -> prescreening -> light_evaluation -> triage -> ...
                                     (Mode 2 replenish: reserve first, then the Scout; Mode 1 alternatives)
    triage -> full_evaluation -> triage -> ...               (the best advanced candidates, step 5.6a)
    triage -> ranking -> reporting -> results_ready          (the ranked results; the report: scripts/report.py)

make_printer() turns the driver's events into the commands' output; print_ended() says how a run ended.
Exit codes as in the commands: 0 ok, 1 a step failed, 3 the budget is used up.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.ai.progress import Recorder
from src.ai.workflows.driver import DriverModels, Emit, Ended, Event, advance
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.light_evaluation import LightOutcome, short_step_line
from src.ai.workflows.prescreening import PrescreenOutcome
from src.ai.workflows.ranking import RankedResults
from src.ai.workflows.refining import RefiningOutcome
from src.ai.workflows.runner import EvaluationModels
from src.ai.workflows.scouting import ScoutingOutcome
from src.ai.workflows.states import Mode


ACCOUNT_REFUSED = ("Anthropic refused the request for your account (a usage or spending limit, the credit, or the API key). Stopped at {where}; nothing was concluded. Fix it in the Anthropic Console, then resume: python -m scripts.{command} --continue {investigation_id}")


@dataclass
class LoopModels:
    """Fakes for tests; None means the configured models."""

    evaluation: EvaluationModels | None = None
    scout_model: object = None
    search_factory: object = None
    quick_model: object = None

    def driver(self, **extra) -> DriverModels:
        return DriverModels(evaluation=self.evaluation, scout_model=self.scout_model,
                            search_factory=self.search_factory, quick_model=self.quick_model, **extra)


# --- formatting ------------------------------------------------------------------------------------------

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


def format_light(outcome: LightOutcome) -> str:
    if not outcome.triaged:
        return ""
    lines = ["\nTriage after light evaluation:"]
    for candidate, decision in outcome.triaged:
        lines.append(f"  {candidate.title}\n    rule {decision.rule_number}: {decision.action} -> "
                     f"{candidate.status.replace('_', ' ')} ({decision.reason})")
    return "\n".join(lines)


def format_refining(outcome: RefiningOutcome) -> str:
    lines = []
    for r in outcome.refinements:
        lines.append(f"  v{r.parent.version} -> v{r.child.version}: {r.child.title}")
        lines.append(f"    change: {r.child.change_note}")
        lines.append("    -> light re-evaluation" if r.passed else f"    SCREENED OUT: {r.reason}")
    return "\n".join(lines)


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


# --- printing the driver's events ------------------------------------------------------------------------

@dataclass(frozen=True)
class Context:
    sessions: Sessions
    investigation_id: uuid.UUID
    command: str


Printer = Callable[[dict, Context], Awaitable[None]]


async def _generated(data: dict, ctx: Context) -> None:
    outcome = data["outcome"]
    print(f"  {TASK_HEADERS[data['task']]}")
    if outcome.from_reserve:
        print(f"  {outcome.from_reserve} idea(s) wait in reserve: no new ideas needed; pre-screening picks the "
              "next ones")
    else:
        print(format_scouting(outcome))


async def _generate_failed(data: dict, ctx: Context) -> None:
    if data["nothing_to_generate"]:
        print(f"  {data['error']} Nothing was generated.")
    else:
        print(f"  The Scout failed: {data['error']}. Nothing was stored; run --continue to try again.")


async def _candidate(data: dict, ctx: Context, *, full: bool) -> None:
    candidate = data["candidate"]
    version = f" (v{candidate.version})" if candidate.version > 1 else ""
    if full:
        print(f"\nFull evaluation {data['number']}/{data['total']}: {candidate.title}{version}"
              " (extends its light evaluation: earlier searches and readings are reused)")
    else:
        print(f"\nLight evaluation {data['number']}/{data['total']}: {candidate.title}{version}")


async def _loop_step(data: dict, ctx: Context) -> None:
    line = short_step_line(data["step"], data["outcome"]) if data["phase"] == "done" else None
    if line:
        print(f"  {line}")


async def _full_done(data: dict, ctx: Context) -> None:
    outcome = data["outcome"]
    if outcome.triaged:
        print("\nTriage after full evaluation:")
        for candidate, decision in outcome.triaged:
            print(f"  {candidate.title}\n    rule {decision.rule_number}: {decision.action} -> "
                  f"{candidate.status.replace('_', ' ')} ({decision.reason})")


async def _loop_failed(data: dict, ctx: Context) -> None:
    print(f"\nThe {data['phase']} stopped ({data['stopped']}) at {data['stopped_at']}. Run --continue to try again.")


async def _routed(data: dict, ctx: Context) -> None:
    decision = data["decision"]
    for candidate in decision.out_of_refinements:
        print(f"\n  rejected (no refinements left): {candidate.title}")
    print(f"\nNext: {decision.stage.replace('_', ' ')} ({decision.reason})")


async def _ranked(data: dict, ctx: Context) -> None:
    if data["mode"] == Mode.DISCOVER:
        print(format_ranking(data["results"]))


async def _done(data: dict, ctx: Context) -> None:
    print(f"\nDone. Total cost: ${data['cost_usd']:.2f}")
    print(f"Full report: python -m scripts.report {ctx.investigation_id} [--out report.md]")


def _say(text: str) -> Printer:
    async def printer(data: dict, ctx: Context) -> None:
        print(text)
    return printer


def _error(template: str) -> Printer:
    async def printer(data: dict, ctx: Context) -> None:
        print(template.format(error=data["error"]))
    return printer


LOOP_PRINTERS: dict[str, Printer] = {
    "generating": _say("\nGenerating candidates..."),
    "generate_failed": _generate_failed,
    "generated": _generated,
    "prescreening": _say("\nPre-screening: duplicates, your constraints, a quick feasibility check..."),
    "prescreen_failed": _error("  The quick feasibility check failed: {error}. Nothing was stored; run --continue "
                               "to try again."),
    "prescreened": lambda data, ctx: _print(format_prescreen(data["outcome"])),
    "light_candidate": lambda data, ctx: _candidate(data, ctx, full=False),
    "full_candidate": lambda data, ctx: _candidate(data, ctx, full=True),
    "loop_step": _loop_step,
    "light_done": lambda data, ctx: _print(format_light(data["outcome"])),
    "full_done": _full_done,
    "loop_failed": _loop_failed,
    "refining": _say("\nRefining (the Scout writes the next version of each idea sent back by triage)..."),
    "refine_failed": _error("  The Scout failed: {error}. Finished refinements are kept; run --continue to try "
                            "again."),
    "refined": lambda data, ctx: _print(format_refining(data["outcome"])),
    "routed": _routed,
    "ranked": _ranked,
    "done": _done,
}


async def _print(text: str) -> None:
    print(text)


def make_printer(sessions: Sessions, investigation_id: uuid.UUID, command: str) -> Emit:
    """The driver's events, printed as the commands always printed them."""
    # imported here: the commands import this module, so a top-level import would be circular
    from scripts.discover import LANDSCAPE_PRINTERS
    from scripts.evaluate import IDEA_PRINTERS

    printers = {**LOOP_PRINTERS, **IDEA_PRINTERS, **LANDSCAPE_PRINTERS}
    ctx = Context(sessions, investigation_id, command)

    async def emit(event: Event) -> None:
        printer = printers.get(event.kind)
        if printer is None:
            raise ValueError(f"No printer for the driver's event {event.kind!r}.")
        await printer(event.data, ctx)

    return emit


def print_ended(ended: Ended, investigation_id: uuid.UUID, command: str) -> int:
    """How the run ended (what wasn't already printed as an event), and the command's exit code."""
    if ended.reason == "budget":
        print(f"\nBudget used up: ${ended.cost_usd:.2f} of ${ended.cap_usd}. Stopped {ended.where}. Resume with: "
              f"python -m scripts.{command} --continue {investigation_id} --budget <more>")
    elif ended.reason == "account_refused":
        print("\n" + ACCOUNT_REFUSED.format(where=ended.where, command=command, investigation_id=investigation_id))
    elif ended.reason == "nothing":
        print(f"Nothing to continue at stage {ended.where}.")
    return ended.exit_code


async def drive(sessions: Sessions, investigation_id: uuid.UUID, models: DriverModels, *, command: str,
                only: str | None = None) -> int:
    """Runs the investigation on from its stage with the driver, printing as it goes; returns the exit code.
    The events are also stored as progress events (step 9c), so the website shows a command-line run too."""
    printer, recorder = make_printer(sessions, investigation_id, command), Recorder(sessions, investigation_id)

    async def emit(event: Event) -> None:
        await printer(event)
        await recorder(event)

    ended = await advance(sessions, investigation_id, models=models, only=only, emit=emit)
    if not (only is not None and ended.reason == "done"):  # one stage of several isn't the run's end
        await recorder.ended(ended)
    return print_ended(ended, investigation_id, command)


# --- the entry points (each carries on from the investigation's current stage) -----------------------------

async def continue_at(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                      command: str) -> int:
    """Carries on from the loop's stages (triage, refining, light evaluation, ranking, ...)."""
    return await drive(sessions, investigation_id, (models or LoopModels()).driver(), command=command)


async def generate(sessions: Sessions, investigation_id: uuid.UUID, models: LoopModels | None = None, *,
                   command: str) -> int:
    """Stage generating_candidates: discover, replenish or alternatives (src/ai/workflows/generating.py)."""
    return await continue_at(sessions, investigation_id, models, command=command)


async def evaluate_light(sessions: Sessions, investigation_id: uuid.UUID, *, models: EvaluationModels | None = None,
                         search_factory=None, scout_model=None, command: str = "discover", quick_model=None) -> int:
    return await continue_at(sessions, investigation_id, LoopModels(models, scout_model, search_factory, quick_model),
                             command=command)
