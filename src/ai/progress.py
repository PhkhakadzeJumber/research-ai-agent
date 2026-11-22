"""Progress events: the driver's events as short stored messages for the progress screen (doc 11 §3.4; step 9c).

describe(event) turns one driver event (src/ai/workflows/driver.py) into an Entry: kind, stage, step, a plain
message and the numbers behind it, or None for events not worth showing. describe_ended(ended) says how a run
ended. Messages are written by code from stored outcomes, never by a model, and carry no command-line hints.
A Recorder stores entries as they happen (src/db/repositories/progress.py) and can pass each stored row on, e.g.
to the API's live stream. Recording never breaks a run: a failure to store is logged and the run goes on.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.ai.evidence.verdicts import LABELS
from src.ai.workflows.driver import Ended, Event
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.light_evaluation import short_step_line
from src.ai.workflows.states import Stage
from src.db.models import ProgressEvent
from src.db.repositories.progress import append_event

log = logging.getLogger("eureka.progress")

STEP_LABELS = {
    "planning_queries": "Planning the searches",
    "retrieving": "Searching arXiv and OpenAlex",
    "screening": "Screening the papers for relevance",
    "reading": "Reading the closest papers",
    "assessing_novelty": "Comparing the idea with the closest papers",
    "skeptic_review": "Skeptic round: searching for evidence against the idea",
    "revising_novelty": "Answering the Skeptic's challenges",
    "assessing_value_feasibility": "Judging significance and feasibility",
    "concluding": "Concluding: freezing the coverage and the verdicts",
}
"""What each evaluation step is doing, shown when it starts."""


@dataclass(frozen=True)
class Entry:
    kind: str
    """stage / step_started / step_done / candidate / warning / error / ended (src/db/models/progress.py)."""
    message: str
    stage: str | None = None
    step: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    candidate_id: uuid.UUID | None = None


def _label(value: str | None) -> str:
    return LABELS.get(value, (value or "").replace("_", " "))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _step_numbers(step: str, outcome: Any) -> dict[str, Any]:
    """The numbers the progress screen counts, from a finished evaluation step."""
    if step == "retrieving":
        return {"searches": outcome.searches, "unique_papers": outcome.unique_papers}
    if step == "screening":
        return {"selected": outcome.selected, "scores": {str(k): v for k, v in outcome.score_counts.items()}}
    if step == "reading":
        return {"read_now": outcome.read_now, "reused": outcome.reused, "failed": outcome.failed}
    if step == "skeptic_review":
        return {"round": outcome.round, "new_relevant": outcome.new_relevant,
                "challenges": len(outcome.challenges), "saturated": bool(outcome.saturated)}
    if step == "concluding":
        return {"novelty": outcome.novelty.category, "significance": outcome.significance.category,
                "feasibility": outcome.feasibility.category, "recommendation": outcome.recommendation.category}
    return {}


def _step_done(step: str, outcome: Any) -> str:
    if step == "retrieving" and outcome.failed:
        return "Every search failed on every source: nothing could be looked at."
    if step == "screening":
        return f"Screened the papers: {outcome.selected} selected to read"
    if step == "skeptic_review":
        found = f"{outcome.new_relevant} new relevant {'paper' if outcome.new_relevant == 1 else 'papers'}"
        return (f"Skeptic round {outcome.round}: {found}, {_plural(len(outcome.challenges), 'challenge')}"
                + (" (saturated: nothing new left to find)" if outcome.saturated else ""))
    if step == "revising_novelty":
        resolutions = list(getattr(outcome, "resolutions", {}).values())
        if resolutions:
            counts = {r: resolutions.count(r) for r in ("accepted", "rebutted", "unresolved") if r in resolutions}
            return "Answered the Skeptic: " + ", ".join(f"{n} {r}" for r, n in counts.items())
    if step == "assessing_novelty" and not getattr(outcome, "error", None) and outcome.reading is not None:
        # step 10b: say what was compared, not just "assessed" (thin in the first live run through the API)
        closest = len(outcome.reading.output.closest_works)
        return (f"Compared the idea with {_plural(len(outcome.papers), 'close paper')}: "
                f"{_plural(closest, 'closest work')} to tell apart")
    if step == "assessing_value_feasibility" and not getattr(outcome, "error", None) and outcome.reading is not None:
        claims = outcome.reading.claims
        verified = sum(1 for claim in claims if claim.status == "verified")
        weeks, available = (outcome.timeline or {}).get("total_weeks"), (outcome.timeline or {}).get("available_weeks")
        plan = (f"; the plan takes {weeks:g} weeks" + (f" of {available:g} available" if available else "")
                if weeks else "")
        return f"Judged significance and feasibility: {verified} of {_plural(len(claims), 'claim')} verified{plan}"
    if step == "concluding":
        return (f"Verdicts: novelty {_label(outcome.novelty.category)}, significance "
                f"{_label(outcome.significance.category)}, feasibility {_label(outcome.feasibility.category)}, "
                f"recommendation {_label(outcome.recommendation.category)}")
    if getattr(outcome, "error", None):
        return f"{STEP_LABELS.get(step, step)}: {outcome.error}"
    line = short_step_line(step, outcome)
    return line[:1].upper() + line[1:] if line else f"{STEP_LABELS.get(step, step)}: done"


def _triaged(candidate, decision) -> Entry:
    return Entry("candidate", f"{candidate.title}: rule {decision.rule_number}, {decision.action.replace('_', ' ')}"
                 f" → {candidate.status.replace('_', ' ')} ({decision.reason})", stage=Stage.TRIAGE,
                 data={"rule": decision.rule_number, "action": decision.action, "status": candidate.status},
                 candidate_id=candidate.id)


def describe(event: Event) -> list[Entry]:
    """The entries one driver event becomes (usually one; none for events the screen doesn't need)."""
    d, kind = event.data, event.kind
    if kind in ("idea_step", "loop_step"):
        step = d["step"]
        if d["phase"] == "start":
            return [Entry("step_started", STEP_LABELS.get(step, step.replace("_", " ")), step=step)]
        failed = step == "retrieving" and d["outcome"].failed
        return [Entry("error" if failed else "step_done", _step_done(step, d["outcome"]), step=step,
                      data=_step_numbers(step, d["outcome"]))]
    if kind == "evaluation_started":
        return [Entry("stage", "Full evaluation of your idea started", stage=Stage.FULL_EVALUATION,
                      data={"evaluation_id": str(d["evaluation_id"])})]
    if kind == "evaluation_failed":
        return [Entry("error", f"The evaluation failed at {d['step'].replace('_', ' ')}", step=d["step"])]
    if kind == "idea_triaged":
        outcome, decision = d["outcome"], d["outcome"].decision
        message = (f"Triage: rule {decision.rule_number}, {decision.action.replace('_', ' ')} ({decision.reason}). "
                   f"Your idea is now {outcome.candidate_status.replace('_', ' ')}.")
        entries = [Entry("candidate", message, stage=Stage.TRIAGE,
                         data={"rule": decision.rule_number, "action": decision.action,
                               "status": outcome.candidate_status})]
        if outcome.route is not None and outcome.route.stage != Stage.RANKING:
            entries.append(Entry("stage", f"Next: {outcome.route.stage.replace('_', ' ')} ({outcome.route.reason})",
                                 stage=outcome.route.stage))
        return entries
    if kind in ("light_candidate", "full_candidate"):
        candidate, depth = d["candidate"], "Light" if kind == "light_candidate" else "Full"
        version = f" (v{candidate.version})" if candidate.version > 1 else ""
        return [Entry("candidate", f"{depth} evaluation {d['number']}/{d['total']}: {candidate.title}{version}",
                      stage=Stage.LIGHT_EVALUATION if depth == "Light" else Stage.FULL_EVALUATION,
                      data={"number": d["number"], "total": d["total"]}, candidate_id=candidate.id)]
    if kind in ("light_done", "full_done"):
        return [_triaged(candidate, decision) for candidate, decision in d["outcome"].triaged]
    if kind == "loop_failed":
        return [Entry("error", f"The {d['phase']} stopped ({d['stopped'].replace('_', ' ')}) at {d['stopped_at']}")]
    if kind == "routed":
        decision = d["decision"]
        entries = [Entry("candidate", f"Rejected, no refinements left: {c.title}", stage=Stage.TRIAGE,
                         candidate_id=c.id) for c in decision.out_of_refinements]
        entries.append(Entry("stage", f"Next: {decision.stage.replace('_', ' ')} ({decision.reason})",
                             stage=decision.stage))
        return entries
    if kind == "generating":
        return [Entry("step_started", "Generating candidate ideas", stage=Stage.GENERATING_CANDIDATES)]
    if kind == "generated":
        outcome = d["outcome"]
        if outcome.from_reserve:
            message = f"{_plural(outcome.from_reserve, 'idea')} in reserve: no new ideas needed"
        else:
            grounded = len(outcome.candidates) - len(outcome.screened_out)
            message = f"The Scout proposed {_plural(len(outcome.candidates), 'idea')} ({grounded} grounded)"
        return [Entry("step_done", message, stage=Stage.GENERATING_CANDIDATES,
                      data={"task": d["task"], "candidates": len(outcome.candidates),
                            "from_reserve": outcome.from_reserve or 0})]
    if kind in ("generate_failed", "prescreen_failed", "refine_failed", "mining_failed"):
        what = {"generate_failed": "Generating ideas", "prescreen_failed": "The quick feasibility check",
                "refine_failed": "Refining", "mining_failed": "Mining the landscape"}[kind]
        return [Entry("error", f"{what} failed: {d['error']}")]
    if kind == "prescreening":
        return [Entry("step_started", "Pre-screening: duplicates, your constraints, a quick feasibility check",
                      stage=Stage.PRESCREENING)]
    if kind == "prescreened":
        o = d["outcome"]
        return [Entry("step_done", f"Pre-screened {o.considered}: {len(o.light)} to light evaluation, "
                                   f"{len(o.reserve)} in reserve, {len(o.screened_out)} screened out",
                      stage=Stage.PRESCREENING,
                      data={"considered": o.considered, "light": len(o.light), "reserve": len(o.reserve),
                            "screened_out": len(o.screened_out)})]
    if kind == "refining":
        return [Entry("step_started", "Refining: the Scout writes the next version of each idea sent back",
                      stage=Stage.REFINING)]
    if kind == "refined":
        return [Entry("candidate", f"v{r.parent.version} → v{r.child.version}: {r.child.title}"
                                   + ("" if r.passed else f" (screened out: {r.reason})"),
                      stage=Stage.REFINING, candidate_id=r.child.id) for r in d["outcome"].refinements]
    if kind == "ranked":
        results = d["results"]
        return [Entry("step_done", f"Ranked: {_plural(len(results.finalists), 'finalist')}, "
                                   f"{len(results.advanced)} passed screening, {len(results.others)} set aside",
                      stage=Stage.RANKING,
                      data={"finalists": len(results.finalists), "advanced": len(results.advanced),
                            "others": len(results.others)})]
    if kind == "landscape_searching":
        return [Entry("step_started", "Searching the landscape: recent work, surveys, highly cited, newest preprints",
                      stage=Stage.LANDSCAPE_RETRIEVAL)]
    if kind == "landscape_searched":
        o = d["outcome"]
        if o.failed:
            return [Entry("error", "Every landscape search failed on every source", stage=Stage.LANDSCAPE_RETRIEVAL)]
        return [Entry("step_done", f"Landscape: {o.searches} searches → {o.unique_papers} unique papers",
                      stage=Stage.LANDSCAPE_RETRIEVAL, data={"searches": o.searches, "unique_papers": o.unique_papers})]
    if kind == "landscape_reading":
        return [Entry("step_started", "Reading the landscape: screening every paper, then reading the best ~45",
                      stage=Stage.LANDSCAPE_READING)]
    if kind == "landscape_read":
        o = d["outcome"]
        return [Entry("step_done", f"Screened {o.screened} papers; read {o.read_now}, {o.reused} from the library",
                      stage=Stage.LANDSCAPE_READING,
                      data={"screened": o.screened, "read_now": o.read_now, "reused": o.reused, "failed": o.failed})]
    if kind == "mining":
        return [Entry("step_started", "Mining the landscape: recurring problems, contradictions, subtopics",
                      stage=Stage.OPPORTUNITY_MINING)]
    if kind == "mined":
        o = d["outcome"]
        return [Entry("step_done", f"Found {_plural(len(o.clusters), 'recurring open problem')} and "
                                   f"{_plural(len(o.contradictions), 'contradiction')}",
                      stage=Stage.OPPORTUNITY_MINING,
                      data={"open_problems": len(o.clusters), "contradictions": len(o.contradictions)})]
    return []  # "done": the run's end is recorded by describe_ended


def describe_ended(ended: Ended) -> Entry:
    """How a run ended, in words."""
    where = (ended.where or "").replace("_", " ")
    messages = {
        "done": "Done: the results are ready.",
        "stopped": (f"Stopped {where if where.startswith('before') else 'at ' + where}. "
                    "Nothing is lost: resume any time."),
        "budget": (f"Budget used up: ${ended.cost_usd:.2f} of ${ended.cap_usd}, stopped {where}. "
                   "Resume with a higher budget." if ended.cost_usd is not None else "Budget used up."),
        "account_refused": ("Anthropic refused the request for the account (a usage or spending limit, the credit, "
                            f"or the API key). Stopped at {where}; nothing was concluded. Resume once it's fixed."),
        "failed": f"Failed at {where}." if where else "Failed.",
        "nothing": (f"Waiting: nothing to run at {where}." if where != "awaiting clarification"
                    else "Waiting for your answers to the clarification."),
    }
    data = {"reason": ended.reason, "where": ended.where}
    if ended.cost_usd is not None:
        data.update(cost_usd=float(ended.cost_usd), cap_usd=float(ended.cap_usd))
    return Entry("ended", messages.get(ended.reason, ended.reason), data=data)


Stored = Callable[[ProgressEvent], Awaitable[None]]


class Recorder:
    """Stores entries for one investigation as they happen; `on_stored` gets each stored row (e.g. the live
    stream). Use it as the driver's `emit`."""

    def __init__(self, sessions: Sessions, investigation_id: uuid.UUID, on_stored: Stored | None = None) -> None:
        self.sessions, self.investigation_id, self.on_stored = sessions, investigation_id, on_stored

    async def __call__(self, event: Event) -> None:
        try:
            entries = describe(event)
        except Exception:  # noqa: BLE001 - an outcome we can't describe must not stop the run
            log.exception("could not describe the %s event of %s", event.kind, self.investigation_id)
            return
        for entry in entries:
            await self.store(entry)

    async def ended(self, ended: Ended) -> None:
        await self.store(describe_ended(ended))

    async def store(self, entry: Entry) -> ProgressEvent | None:
        try:
            async with self.sessions() as session:
                row = await append_event(session, self.investigation_id, kind=entry.kind, message=entry.message,
                                         stage=entry.stage, step=entry.step, data=entry.data,
                                         candidate_id=entry.candidate_id)
        except Exception:  # noqa: BLE001 - progress is a view of the run, never a reason to stop it
            log.exception("could not store a progress event for %s", self.investigation_id)
            return None
        if self.on_stored is not None:
            await self.on_stored(row)
        return row
