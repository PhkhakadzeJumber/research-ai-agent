"""Pre-screening the Scout's candidates (Mode 2, stage `prescreening`, step 5.4; doc 05 §11.2).

prescreen_step(sessions, investigation_id) looks at every candidate still `proposed` (the
grounding rule already ran in step 5.3) and decides, mostly in code and before any money is
spent on evaluations:
  1. near-duplicates (code): word overlap of the title and key facets. A candidate too close
     to a better-grounded one is screened out. (Word overlap, because embeddings aren't set up.)
  2. hard constraints (code): the Scout's weeks against the user's available time; and, only
     when the user gave no compute constraint, the Scout's compute tier against the default
     ceiling of the user's complexity level (doc 02 §3). A stated compute constraint is free
     text, so the quick feasibility check judges it instead;
  3. quick feasibility (A6 task, one batched call): not feasible, or a conflict with one of the
     user's exclusions, screens the candidate out. Feasible with adjustments and unclear pass:
     the light evaluation's full Value & Feasibility check decides;
  4. choosing: at most `max_light` candidates (default: the investigation's light-evaluation cap,
     src/ai/workflows/budget.py) go to light evaluation (`light_evaluating`),
     ordered by quick-feasibility level and grounding (distinct papers behind the idea), taken
     in turn from each origin strategy so the evaluated set stays varied. The others stay
     `proposed`, in reserve (step 5.5's replenish uses them first).

Every check's result and the decision are stored on the candidate (`prescreen`); a screened-out
candidate also keeps its reason. The stage then moves to `light_evaluation`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.agents.quick_feasibility import check_candidates
from src.ai.context import CandidateForQuickCheck
from src.ai.workflows.budget import light_cap
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.field import field_request
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status
from src.ai.workflows.value_feasibility import available_weeks
from src.config import RoleLlmSettings
from src.db.models import Candidate, CandidateInspiration, Investigation, Quote
from src.db.repositories.candidates import candidate_facets
from src.db.repositories.llm_calls import record_llm_calls

DUPLICATE_OVERLAP = 0.6
TIERS = ("T0", "T1", "T2", "T3")
TIER_CEILING = {"beginner": "T1", "intermediate": "T2", "advanced": "T3"}  # doc 02 §3
LEVEL_ORDER = {"feasible": 0, "feasible_with_adjustments": 1, "unclear": 2}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"with", "from", "that", "this", "does", "into", "than", "their", "when", "what", "which", "small",
         "models", "model", "language", "languages", "using", "based", "study", "evaluation"}


@dataclass
class PrescreenOutcome:
    considered: int = 0
    screened_out: list[tuple[Candidate, str]] = field(default_factory=list)
    light: list[Candidate] = field(default_factory=list)
    reserve: list[Candidate] = field(default_factory=list)
    quick: dict[uuid.UUID, dict] = field(default_factory=dict)
    """candidate id -> its quick feasibility result."""


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 4 and w not in _STOP}


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _has_compute_constraint(constraints: dict) -> str | None:
    for name, value in (constraints or {}).items():
        if any(word in str(name).lower() for word in ("compute", "gpu", "hardware")) and value:
            return str(value)
    return None


def choose_for_light(passing: list[tuple[Candidate, int, str]], limit: int) -> list[Candidate]:
    """passing: (candidate, grounding papers, quick level). The best of each origin strategy in turn."""
    ordered = sorted(passing, key=lambda row: (LEVEL_ORDER.get(row[2], 3), -row[1]))
    queues: dict[str, list[Candidate]] = {}
    for candidate, _, _ in ordered:
        queues.setdefault(candidate.origin_strategy or "", []).append(candidate)
    chosen: list[Candidate] = []
    while len(chosen) < limit and any(queues.values()):
        for queue in list(queues.values()):  # strategies in the order of their best candidate
            if queue and len(chosen) < limit:
                chosen.append(queue.pop(0))
    return chosen


async def prescreen_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    max_light: int | None = None,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> PrescreenOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.PRESCREENING:
            raise StaleState(f"Investigation {investigation_id} isn't at prescreening.")
        max_light = max_light if max_light is not None else light_cap(investigation)
        if max_light < 1:
            raise ValueError("max_light must be at least 1.")
        request = field_request(investigation)
        constraints = investigation.constraints_snapshot or {}
        complexity = investigation.complexity
        candidates = list((await session.execute(
            select(Candidate).where(Candidate.investigation_id == investigation_id,
                                    Candidate.status == CandidateStatus.PROPOSED)
            .order_by(Candidate.created_at, Candidate.title))).scalars())
        facets = {c.id: await candidate_facets(session, c.id) for c in candidates}
        papers = {c.id: len(set((await session.execute(
            select(Quote.paper_id).join(CandidateInspiration, CandidateInspiration.quote_id == Quote.id)
            .where(CandidateInspiration.candidate_id == c.id, Quote.verified.is_(True)))).scalars()))
            for c in candidates}

    outcome = PrescreenOutcome(considered=len(candidates))
    records = {c.id: {"checks": {}} for c in candidates}
    out: dict[uuid.UUID, str] = {}

    # 1. near-duplicates: the better-grounded candidate is kept
    kept: list[tuple[Candidate, set[str]]] = []
    for c in sorted(candidates, key=lambda c: -papers[c.id]):
        words = _words(" ".join([c.title, *(f.text for f in facets[c.id] if f.is_key)]))
        twin = next(((k, overlap(words, w)) for k, w in kept if overlap(words, w) >= DUPLICATE_OVERLAP), None)
        if twin:
            records[c.id]["checks"]["duplicate"] = {"of": str(twin[0].id), "overlap": round(twin[1], 2)}
            out[c.id] = f"Near-duplicate of '{twin[0].title}' (word overlap {twin[1]:.0%})."
        else:
            records[c.id]["checks"]["duplicate"] = None
            kept.append((c, words))

    # 2. hard constraints
    weeks = available_weeks(constraints)
    compute = _has_compute_constraint(constraints)
    ceiling = TIER_CEILING.get(complexity, "T3")
    for c in candidates:
        notes = c.scout_notes or {}
        need = notes.get("timeline_weeks")
        over_time = weeks is not None and need is not None and need > weeks
        records[c.id]["checks"]["time"] = {"needs_weeks": need, "available_weeks": weeks, "over": over_time}
        tier = notes.get("compute_tier")
        if compute:
            records[c.id]["checks"]["compute"] = {"tier": tier, "constraint": compute,
                                                  "judged_by": "quick feasibility"}
            over_compute = False
        else:
            over_compute = tier in TIERS and TIERS.index(tier) > TIERS.index(ceiling)
            records[c.id]["checks"]["compute"] = {"tier": tier, "ceiling": ceiling, "over": over_compute}
        if c.id not in out and over_time:
            out[c.id] = f"Needs about {need} weeks; {weeks:g} are available."
        elif c.id not in out and over_compute:
            out[c.id] = f"Needs compute tier {tier}; the {complexity} level allows up to {ceiling}."

    # 3. quick feasibility, one call for everyone still in
    remaining = [c for c in candidates if c.id not in out]
    passing: list[tuple[Candidate, int, str]] = []
    records_llm = []
    if remaining:
        labels = {f"C{n}": c for n, c in enumerate(remaining, start=1)}
        views = [CandidateForQuickCheck(
            label, c.title, c.research_question, tuple((f.kind, f.text) for f in facets[c.id]),
            (c.scout_notes or {}).get("compute_tier"), (c.scout_notes or {}).get("timeline_weeks"),
            (c.scout_notes or {}).get("data_source"), (c.scout_notes or {}).get("complexity_note"))
            for label, c in labels.items()]
        quick = await check_candidates(request, views, settings=settings, model=model)
        records_llm = quick.records
        for label, c in labels.items():
            check = quick.checks.get(label)
            result = ({"level": check.proposed_level, "blocking": check.blocking_requirements,
                       "exclusion_conflict": check.exclusion_conflict, "note": check.note}
                      if check else {"level": "unclear", "blocking": [], "exclusion_conflict": None,
                                     "note": "not checked: the model skipped this candidate"})
            records[c.id]["checks"]["quick_feasibility"] = {**result, "prompt_version": quick.prompt_version}
            outcome.quick[c.id] = result
            if result["level"] == "not_feasible":
                blocking = "; ".join(result["blocking"]) or result["note"]
                out[c.id] = f"Quick feasibility: not feasible under your constraints ({blocking})."
            elif result["exclusion_conflict"]:
                out[c.id] = f"Touches one of your exclusions: {result['exclusion_conflict']}"
            else:
                passing.append((c, papers[c.id], result["level"]))

    # 4. choosing
    light = choose_for_light(passing, max_light)
    light_ids = {c.id for c in light}
    async with sessions() as session:
        for c in candidates:
            stored = await session.get(Candidate, c.id)
            if c.id in out:
                decision = "screened_out"
                await set_candidate_status(session, c.id, to=CandidateStatus.SCREENED_OUT,
                                           expected_from=CandidateStatus.PROPOSED, reason=out[c.id])
                outcome.screened_out.append((stored, out[c.id]))
            elif c.id in light_ids:
                decision = "light_evaluation"
                await set_candidate_status(session, c.id, to=CandidateStatus.LIGHT_EVALUATING,
                                           expected_from=CandidateStatus.PROPOSED)
            else:
                decision = "reserve"
                outcome.reserve.append(stored)
            stored.prescreen = {**records[c.id], "decision": decision, "grounding_papers": papers[c.id],
                                **({"order": [x.id for x in light].index(c.id) + 1} if c.id in light_ids else {})}
        outcome.light = [await session.get(Candidate, c.id) for c in light]
        await record_llm_calls(session, records_llm, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.LIGHT_EVALUATION,
                                    expected_from=Stage.PRESCREENING)
    return outcome
