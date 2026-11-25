"""A run, one step per page (Figma pages 3 and 4): which steps a mode has, where the run is, and what each step did.

Everything here is read from what the run stored as it went (progress events, searches, retrieval hits, paper
analyses, opportunity items, candidates, evaluations, verdicts, AI call costs): no AI call, so opening a step page
is free. "What Eureka did" is written here by code, from those numbers.

Where the run is:
  - Mode 1 follows your idea's full evaluation (its evaluation step), then the refinements and alternatives, which
    belong to the Verdict step (they come out of it);
  - Mode 2 follows the investigation's stage; step 7 (Full evaluation) is repeated per finalist on its own pages,
    whose steps are Mode 1's (`candidate`).
A step's time comes from its progress events (runs before step 9c have none: shown as not recorded), its credits
from the AI calls of its agents (about: rounded per step).
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.common import paper_ref
from src.api.schemas.steps import (
    Finding,
    IdeaRow,
    IdeasOutput,
    LogLine,
    Problem,
    ProblemsOutput,
    Query,
    ReadingOutput,
    ReadPaper,
    RefOutput,
    ScreenedPaper,
    ScreeningOutput,
    SearchOutput,
    Stat,
    StepPage,
    StepSummary,
    Steps,
)
from src.config import get_billing_settings
from src.db.models import (
    Candidate,
    Evaluation,
    Investigation,
    LlmCall,
    OpportunityItem,
    Paper,
    PaperAnalysis,
    ProgressEvent,
    Quote,
    ResearchPackage,
    RetrievalHit,
    SearchQuery,
    Verdict,
)
from src.timeutil import now

# --- the steps of each mode -----------------------------------------------------------------------------------

# (key, stepper label, full name)
EVALUATE_STEPS = (("idea", "Your idea", "Your idea"), ("search", "Search", "Search"),
                  ("screening", "Screening", "Screening"), ("reading", "Reading", "Reading"),
                  ("novelty", "Novelty", "Novelty"), ("skeptic", "Skeptic", "Skeptic"),
                  ("value", "Value & feasibility", "Value & feasibility"), ("verdict", "Verdict", "Verdict"),
                  ("package", "Research package", "Research package"))
DISCOVER_STEPS = (("field", "Your field", "Your field"), ("landscape", "Landscape", "Landscape"),
                  ("problems", "Open problems", "Open problems"), ("ideas", "Ideas", "Candidate ideas"),
                  ("prescreen", "Pre-screen", "Pre-screening"), ("light", "Light check", "Light evaluation"),
                  ("full", "Full evaluation", "Full evaluation"), ("ranking", "Ranking", "Ranking"),
                  ("package", "Package", "Research package"))
FINALIST_KEYS = ("search", "screening", "reading", "novelty", "skeptic", "value", "verdict")
"""Mode 2's step 7, per finalist: the steps of Mode 1's evaluation."""

DOES = {
    "idea": "Eureka reads your idea (or your document) and splits it into its parts: the problem, the method, the "
            "data, the evaluation and the contribution. It asks up to 3 questions whose answers would change the "
            "result. Check its reading, correct any part, answer what you like; nothing more runs until you confirm.",
    "field": "Eureka reads your field and interests and proposes subtopics to explore, with up to 3 questions whose "
             "answers would change the result. Drop any subtopic you don't care about and answer what you like; "
             "nothing more runs until you confirm.",
    "search": "Eureka turns the idea into several search phrasings and asks OpenAlex and arXiv for papers, newest and "
              "most cited first. A paper found by more than one phrasing ranks higher, and duplicates across the two "
              "sources are merged.",
    "screening": "A fast model reads every paper's title and abstract and scores how relevant it is to the idea, "
                 "from 0 (unrelated) to 3 (directly relevant). Only the most relevant go on to be read closely, which "
                 "keeps the evaluation focused and cheap.",
    "reading": "The Paper Reader reads the most relevant papers closely and writes down what each one found, its "
               "setting and its limits. Every finding keeps a quote from the paper, and code checks each quote word "
               "for word: a finding whose quote isn't in the paper is never used as evidence.",
    "novelty": "The Novelty Investigator compares the idea, part by part, with the closest papers it read: for each "
               "part, is the paper doing the same, something similar, or something different? The verdict then "
               "follows fixed rules in code: a part counts as the same only with a verified quote behind it.",
    "skeptic": "A second agent, the Skeptic, tries hard to find prior work that would kill the idea. It searches "
               "again with its own phrasings, reads what it finds and raises challenges, round after round, until a "
               "round finds nothing new. Each challenge is then answered against the evidence.",
    "value": "Eureka checks whether the idea matters (who would use the result, and what it would change) and whether "
             "you can do it with your limits: your weeks, your compute and the data you can use. A hard limit can "
             "make a good idea not feasible for you.",
    "verdict": "The verdicts are combined into one recommendation by fixed rules: pursue, refine or reconsider. Every "
               "rule that fired is shown, so you can check why. If parts of the idea are weak, Eureka refines it or "
               "suggests alternatives, and checks those too.",
    "package": "The Proposal Designer writes a 17-section research plan from the frozen evaluation: background, "
               "research question, hypotheses, method, datasets, experiments, risks, timeline and references. Every "
               "statement says whether it is evidence (with its paper), an interpretation, a hypothesis, or something "
               "to verify.",
    "landscape": "Eureka maps the field: it searches each subtopic, screens what it finds, and reads the newest and "
                 "most cited papers closely. Every later step works from this map of what the field actually says, "
                 "not from the model's memory.",
    "problems": "From the papers it read, Eureka collects every limitation and future-work statement, then groups the "
                "ones that several papers share. An open problem only counts if at least 3 papers point to it, each "
                "with a verified quote.",
    "ideas": "The Scout turns the open problems into concrete research ideas, each with a research question, a "
             "method, the data it needs and the open problem it answers, sized to your ambition and your limits.",
    "prescreen": "A quick, cheap check drops ideas that clearly break your limits or clearly exist already, before any "
                 "money is spent evaluating them properly, and picks the most promising for the light evaluation. "
                 "Every idea dropped shows why.",
    "light": "Each chosen idea gets a short evaluation: a smaller search, the closest papers, one Skeptic round and a "
             "quick value check. It decides which ideas become finalists; an idea with a fixable problem may be "
             "refined and checked again.",
    "full": "Each finalist gets the same full evaluation as Evaluate my idea: search, screening, reading, novelty, the "
            "Skeptic, value and feasibility, and the verdict. Open a finalist to see each of its steps on its own "
            "page.",
    "ranking": "The finalists are ranked by their verdicts: the recommendation first, then novelty, value, and how "
               "comfortably they fit your limits. Every idea that stopped earlier is listed with why.",
}

EVAL_STEP_KEYS = {"normalizing": "search", "planning_queries": "search", "retrieving": "search",
                  "screening": "screening", "reading": "reading", "assessing_novelty": "novelty",
                  "skeptic_review": "skeptic", "revising_novelty": "skeptic",
                  "assessing_value_feasibility": "value", "concluding": "verdict", "done": "verdict"}
DISCOVER_STAGE_KEYS = {"intake": "field", "understanding": "field", "awaiting_clarification": "field",
                       "landscape_retrieval": "landscape", "landscape_reading": "landscape",
                       "opportunity_mining": "problems", "generating_candidates": "ideas", "prescreening": "prescreen",
                       "light_evaluation": "light", "refining": "light", "full_evaluation": "full",
                       "ranking": "ranking", "reporting": "ranking", "writing_proposal": "package"}
EVALUATE_STAGE_KEYS = {"intake": "idea", "understanding": "idea", "awaiting_clarification": "idea",
                       "full_evaluation": "search", "triage": "verdict", "refining": "verdict",
                       "light_evaluation": "verdict", "ranking": "verdict", "reporting": "verdict",
                       "writing_proposal": "package"}
FINISHED_STAGES = ("results_ready", "writing_proposal")

# which agent's AI calls belong to which step (credits per step)
EVALUATE_ROLE_KEYS = {"relevance_screening": "screening", "paper_reader": "reading", "novelty": "novelty",
                      "skeptic": "skeptic", "value_feasibility": "value", "idea_analyst": "search"}
DISCOVER_ROLE_KEYS = {"idea_analyst": "field", "relevance_screening": "landscape", "paper_reader": "landscape",
                      "opportunity_mining": "problems", "scout": "ideas", "feasibility_quick": "prescreen",
                      "proposal": "package"}


def steps_of(mode: str) -> tuple[tuple[str, str, str], ...]:
    return EVALUATE_STEPS if mode == "evaluate" else DISCOVER_STEPS


# --- what the run stored ----------------------------------------------------------------------------------------

@dataclass
class RunFacts:
    investigation: Investigation
    own: Candidate | None = None
    """Mode 1: your idea (its first version)."""
    own_eval: Evaluation | None = None
    """Mode 1: your idea's full evaluation."""
    event_keys: list[tuple[ProgressEvent, str]] = field(default_factory=list)
    """Each progress event with the step it belongs to."""
    usd: dict[str, Decimal] = field(default_factory=dict)
    """AI cost per step."""
    has_full: bool = False
    has_package: bool = False


def _event_keys(mode: str, events: list[ProgressEvent]) -> list[tuple[ProgressEvent, str]]:
    """Each event's step. Events say their stage or, inside an evaluation, its step; Mode 1's events after your
    idea's evaluation concluded (refinements, alternatives) belong to the Verdict step."""
    out, current, concluded = [], None, False
    for e in events:
        key = None
        if mode == "evaluate":
            if e.stage == "writing_proposal":
                key = "package"
            elif e.step and not concluded:
                key = EVAL_STEP_KEYS.get(e.step)
            elif e.step and concluded:
                key = "verdict"
            elif e.stage:
                key = EVALUATE_STAGE_KEYS.get(e.stage)
            if e.step == "concluding" and e.kind == "step_done":
                concluded = True
        else:
            key = DISCOVER_STAGE_KEYS.get(e.stage) if e.stage else current
            if e.stage == "triage":
                key = current
        if e.kind == "ended":
            key = current  # how the run ended: shown in the step it stopped in
        if key:
            current = key
            out.append((e, key))
    return out


async def facts(session: AsyncSession, investigation: Investigation) -> RunFacts:
    f = RunFacts(investigation=investigation)
    mode = investigation.mode
    if mode == "evaluate":
        f.own = (await session.execute(select(Candidate).where(
            Candidate.investigation_id == investigation.id, Candidate.origin == "user", Candidate.version == 1)
            .order_by(Candidate.created_at).limit(1))).scalar_one_or_none()
        if f.own is not None:
            f.own_eval = await full_evaluation(session, f.own.id)
    events = list((await session.execute(select(ProgressEvent).where(
        ProgressEvent.investigation_id == investigation.id).order_by(ProgressEvent.seq))).scalars())
    f.event_keys = _event_keys(mode, events)
    depth = {e.id: e.depth for e in (await session.execute(
        select(Evaluation).join(Candidate, Candidate.id == Evaluation.candidate_id)
        .where(Candidate.investigation_id == investigation.id))).scalars()}
    f.has_full = any(d == "full" for d in depth.values())
    windows = _windows(f.event_keys)
    for role, evaluation_id, usd, at in (await session.execute(
            select(LlmCall.role, LlmCall.evaluation_id, LlmCall.cost_usd, LlmCall.created_at)
            .where(LlmCall.investigation_id == investigation.id))).all():
        key = _window_key(windows, at)  # when it happened: the step working then (the Skeptic's reading is its own)
        if key is None:
            if mode == "evaluate":
                if role == "proposal":
                    key = "package"
                elif evaluation_id is None:
                    key = "idea"
                elif f.own_eval is not None and evaluation_id == f.own_eval.id:
                    key = EVALUATE_ROLE_KEYS.get(role, "verdict")
                else:
                    key = "verdict"  # refinements and alternatives
            elif evaluation_id is not None:
                key = depth.get(evaluation_id, "full")
            else:
                key = DISCOVER_ROLE_KEYS.get(role, "landscape")
        f.usd[key] = f.usd.get(key, Decimal(0)) + Decimal(usd or 0)
    f.has_package = (await session.execute(select(func.count()).select_from(ResearchPackage).join(
        Candidate, Candidate.id == ResearchPackage.candidate_id).where(
        Candidate.investigation_id == investigation.id))).scalar_one() > 0
    return f


def _windows(event_keys: list[tuple[ProgressEvent, str]]) -> list[tuple[object, object, str]]:
    """(start, end, step) for each step_started -> step_done pair: when each step was working."""
    out, open_ = [], {}
    for e, key in event_keys:
        if e.kind == "step_started":
            open_[key] = e.created_at
        elif e.kind == "step_done" and key in open_:
            out.append((open_.pop(key), e.created_at, key))
    return out


def _window_key(windows: list[tuple[object, object, str]], at) -> str | None:
    for start, end, key in windows:
        if start <= at <= end:
            return key
    return None


async def full_evaluation(session: AsyncSession, candidate_id: uuid.UUID) -> Evaluation | None:
    return (await session.execute(select(Evaluation).where(
        Evaluation.candidate_id == candidate_id, Evaluation.depth == "full")
        .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()


# --- where the run is -------------------------------------------------------------------------------------------

def current_key(f: RunFacts) -> str | None:
    """The step in progress (working, waiting for you, paused or failed); None when the run has finished."""
    inv = f.investigation
    stage = inv.stage
    if stage == "results_ready":
        return None
    if inv.mode == "evaluate":
        if stage == "full_evaluation" and f.own_eval is not None:
            return EVAL_STEP_KEYS.get(f.own_eval.step, "search") if f.own_eval.status == "running" else "verdict"
        return EVALUATE_STAGE_KEYS.get(stage, "verdict")
    if stage == "triage":
        return "full" if f.has_full else "light"
    return DISCOVER_STAGE_KEYS.get(stage, "ranking")


def _seconds(f: RunFacts, key: str, running: bool) -> int | None:
    """The time Eureka worked on the step: each step_started to its step_done (so the wait for your answers at the
    clarification doesn't count); a step still running counts to now. Without started/done events, the span of its
    events."""
    events = [e for e, k in f.event_keys if k == key]
    if not events:
        return None
    total, started = 0.0, None
    for e in events:
        if e.kind == "step_started":
            started = e.created_at
        elif e.kind == "step_done" and started is not None:
            total += (e.created_at - started).total_seconds()
            started = None
    if started is not None and running:
        total += (now() - started).total_seconds()
    if total == 0 and not any(e.kind == "step_started" for e in events):
        total = ((now() if running else events[-1].created_at) - events[0].created_at).total_seconds()
    return max(0, int(total))


def _credits(f: RunFacts, key: str) -> int | None:
    usd = f.usd.get(key)
    if usd is None:
        return None
    return math.ceil(float(usd) / get_billing_settings().credit_usd - 1e-9)


def summaries(f: RunFacts, run: str) -> tuple[str | None, list[StepSummary]]:
    inv = f.investigation
    defs = steps_of(inv.mode)
    keys = [k for k, _, _ in defs]
    current = current_key(f)
    out = []
    for n, (key, label, name) in enumerate(defs, start=1):
        if key == "package":
            state = ("running" if run != "idle" else "paused") if current == "package" else \
                "done" if f.has_package else "upcoming"
        elif current is None or current == "package":
            state = "done"
        else:
            position = keys.index(current)
            if n - 1 < position:
                state = "done"
            elif n - 1 > position:
                state = "upcoming"
            elif inv.status == "failed":
                state = "failed"
            elif run != "idle":
                state = "running"
            elif inv.stage == "awaiting_clarification":
                state = "waiting"
            else:
                state = "paused"
        out.append(StepSummary(key=key, number=n, label=label, name=name, state=state,
                               seconds=_seconds(f, key, state == "running"), credits=_credits(f, key),
                               optional=key == "package"))
    return current, out


async def steps_overview(session: AsyncSession, investigation: Investigation, run: str) -> Steps:
    f = await facts(session, investigation)
    current, steps = summaries(f, run)
    return Steps(mode=investigation.mode, run=run, current=current, steps=steps)


# --- one step's page ------------------------------------------------------------------------------------------

def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else plural or word + 's'}"


def _minutes(seconds: int | None) -> str:
    if seconds is None:
        return "not recorded"
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds < 3600 else f"{seconds // 3600} h {seconds % 3600 // 60} min"


async def _first_found(session: AsyncSession, evaluation: Evaluation) -> set[uuid.UUID]:
    """The papers the evaluation's first searches found (the Skeptic's rounds search again: their papers belong to
    the Skeptic step)."""
    first = {q for q in (await session.execute(select(SearchQuery.id).where(
        SearchQuery.evaluation_id == evaluation.id, SearchQuery.round.is_(None)))).scalars()}
    return {p for p, found_by in (await session.execute(select(RetrievalHit.paper_id, RetrievalHit.found_by_query_ids)
                                                        .where(RetrievalHit.evaluation_id == evaluation.id))).all()
            if any(q in first for q in (found_by or []))}


async def _search(session: AsyncSession, evaluation: Evaluation) -> tuple[SearchOutput, str, list[Stat]]:
    queries = list((await session.execute(select(SearchQuery).where(
        SearchQuery.evaluation_id == evaluation.id, SearchQuery.round.is_(None)).order_by(SearchQuery.created_at))).scalars())
    order = {q.id: i for i, q in enumerate(queries)}
    hits = list((await session.execute(select(RetrievalHit.paper_id, RetrievalHit.found_by_query_ids).where(
        RetrievalHit.evaluation_id == evaluation.id))).all())
    first: Counter = Counter()
    for _, found_by in hits:
        mine = [q for q in (found_by or []) if q in order]
        if mine:
            first[min(mine, key=order.get)] += 1
    papers = len({p for p, found_by in hits if any(q in order for q in (found_by or []))})
    rows = [Query(text=q.query_text, source=q.source, round=q.round, found=q.result_count, new=first[q.id])
            for q in queries]
    sources = sorted({q.source for q in queries})
    total = sum(q.result_count for q in queries)
    productive = sum(1 for r in rows if r.new > 0)
    phrasings = len({q.query_text for q in queries})
    did = (f"Eureka searched {_plural(phrasings, 'phrasing')} of the idea on {' and '.join(sources) or 'its sources'} "
           f"({_plural(len(rows), 'search', 'searches')}). They returned {total:,} results; after merging duplicates, "
           f"{_plural(papers, 'distinct paper')} remained. "
           + (f"{_plural(productive, 'search', 'searches')} added papers no earlier search had found"
              + (f"; {len(rows) - productive} added nothing new." if productive < len(rows) else ".")
              if rows else "No searches were recorded for this evaluation."))
    return SearchOutput(queries=rows, papers=papers), did, [Stat(label="Phrasings", value=str(phrasings)),
                                                           Stat(label="Results", value=f"{total:,}"),
                                                           Stat(label="Papers found", value=f"{papers:,}")]


async def _screening(session: AsyncSession, evaluation: Evaluation) -> tuple[ScreeningOutput, str, list[Stat]]:
    first = await _first_found(session, evaluation)
    rows = [(h, p) for h, p in (await session.execute(
        select(RetrievalHit, Paper).join(Paper, Paper.id == RetrievalHit.paper_id)
        .where(RetrievalHit.evaluation_id == evaluation.id, RetrievalHit.screened.is_(True)))).all()
        if h.paper_id in first]
    scores = Counter(str(h.llm_score) for h, _ in rows if h.llm_score is not None)
    relevant = sum(1 for h, _ in rows if (h.llm_score or 0) >= 2)
    selected = sum(1 for h, _ in rows if h.selected_for_reading)
    ranked = sorted(rows, key=lambda r: (-(r[0].llm_score or 0), not r[0].selected_for_reading, r[1].title))
    kept = [ScreenedPaper(paper=paper_ref(p), score=h.llm_score, reason=h.llm_reason, read=h.selected_for_reading)
            for h, p in ranked if (h.llm_score or 0) >= 2][:40]
    dropped = [ScreenedPaper(paper=paper_ref(p), score=h.llm_score, reason=h.llm_reason, read=False)
               for h, p in ranked if (h.llm_score or 0) <= 1 and h.llm_reason][:40]
    did = (f"Eureka screened {_plural(len(rows), 'paper')} by title and abstract. {relevant:,} are relevant (score 2 or "
           f"3) and {len(rows) - relevant:,} are not; {_plural(selected, 'paper')} were chosen to read closely. "
           f"Scores: {scores.get('3', 0)} directly relevant, {scores.get('2', 0)} relevant, {scores.get('1', 0)} "
           f"loosely related, {scores.get('0', 0)} unrelated.")
    return (ScreeningOutput(screened=len(rows), relevant=relevant, selected=selected,
                            scores={k: scores.get(k, 0) for k in ("0", "1", "2", "3")}, kept=kept, dropped=dropped),
            did, [Stat(label="Screened", value=f"{len(rows):,}"), Stat(label="Relevant", value=f"{relevant:,}"),
                  Stat(label="To read", value=f"{selected:,}")])


async def _reading(session: AsyncSession, evaluation: Evaluation) -> tuple[ReadingOutput, str, list[Stat]]:
    first = await _first_found(session, evaluation)
    read_ids = [p for p in (await session.execute(select(RetrievalHit.paper_id).where(
        RetrievalHit.evaluation_id == evaluation.id, RetrievalHit.selected_for_reading.is_(True)).distinct())).scalars()
        if p in first]
    papers = {p.id: p for p in (await session.execute(select(Paper).where(Paper.id.in_(read_ids)))).scalars()} \
        if read_ids else {}
    analyses: dict[uuid.UUID, PaperAnalysis] = {}
    for a in (await session.execute(select(PaperAnalysis).where(PaperAnalysis.paper_id.in_(read_ids))
                                    .order_by(PaperAnalysis.created_at))).scalars() if read_ids else []:
        analyses[a.paper_id] = a  # the latest reading of each paper
    quotes = {str(q.id): q for q in (await session.execute(select(Quote).where(
        Quote.paper_id.in_(read_ids)))).scalars()} if read_ids else {}
    out, findings_total, quoted, verified = [], 0, 0, 0
    for paper_id, analysis in analyses.items():
        findings = []
        for item in (analysis.content or {}).get("findings", []):
            ref = item.get("quote") or {}
            quote = quotes.get(str(ref.get("quote_id"))) if ref.get("quote_id") else None
            findings.append(Finding(text=item.get("claim", ""), quote=quote.text if quote else ref.get("text", ""),
                                    verified=bool(quote is not None and quote.verified)))
            findings_total += 1
            quoted += 1 if (quote or ref.get("text")) else 0
            verified += 1 if (quote is not None and quote.verified) else 0
        out.append(ReadPaper(paper=paper_ref(papers[paper_id]), source_level=analysis.source_level, findings=findings))
    out.sort(key=lambda r: (-len(r.findings), r.source_level != "full_text"))
    full_text = sum(1 for a in analyses.values() if a.source_level == "full_text")
    did = (f"Eureka read {_plural(len(analyses), 'paper')} closely ({full_text} from the full text, "
           f"{len(analyses) - full_text} from the abstract) and recorded {_plural(findings_total, 'finding')}. "
           + (f"{verified} of {quoted} quotes were found word for word in their papers"
              + ("; the others are marked and never used as evidence." if verified < quoted else ".")
              if quoted else ""))
    return (ReadingOutput(read=len(analyses), full_text=full_text, findings=findings_total, quotes=quoted,
                          quotes_verified=verified, papers=out[:20]),
            did, [Stat(label="Papers read", value=str(len(analyses))), Stat(label="Full text", value=str(full_text)),
                  Stat(label="Findings", value=str(findings_total)),
                  Stat(label="Quotes verified", value=f"{verified} / {quoted}")])


async def _verdicts(session: AsyncSession, evaluation: Evaluation | None) -> dict[str, Verdict]:
    if evaluation is None:
        return {}
    return {v.dimension: v for v in (await session.execute(select(Verdict).where(
        Verdict.evaluation_id == evaluation.id))).scalars()}


def _words(code: str | None) -> str:
    return (code or "").replace("_", " ")


async def _evaluation_ref(session: AsyncSession, key: str, evaluation: Evaluation | None,
                          candidate: Candidate | None) -> tuple[RefOutput, str, list[Stat]]:
    """Novelty, Skeptic, Value & feasibility, Verdict: summarized here, shown in full by the idea's endpoints."""
    view = {"novelty": "matrix", "skeptic": "challenges", "value": "feasibility", "verdict": "verdict"}[key]
    ref = RefOutput(view=view, candidate_id=candidate.id if candidate else None)
    verdicts = await _verdicts(session, evaluation)
    if evaluation is None:
        return ref, "Not reached yet.", []
    if key == "novelty":
        assessment = evaluation.novelty_assessment or {}
        closest = len(assessment.get("closest_works", []))
        v = verdicts.get("novelty")
        did = (f"Eureka compared the idea, part by part, with the closest papers it read and kept "
               f"{_plural(closest, 'closest work')} to tell apart. "
               + (f"The verdict is {_words(v.category)} (rule {v.rule})." if v else "The verdict comes at the end."))
        return ref, did, [Stat(label="Closest works", value=str(closest))] + (
            [Stat(label="Novelty", value=_words(v.category))] if v else [])
    if key == "skeptic":
        coverage = evaluation.coverage or {}
        rounds = coverage.get("skeptic_rounds", [])
        challenges = sum(r.get("challenges", 0) for r in rounds)
        new = sum(r.get("new_relevant", 0) for r in rounds)
        searches = (await session.execute(select(func.count()).select_from(SearchQuery).where(
            SearchQuery.evaluation_id == evaluation.id, SearchQuery.round.is_not(None)))).scalar_one()
        per_round = "; ".join(f"round {r.get('round')}: {r.get('new_relevant', 0)} new papers, "
                              f"{r.get('challenges', 0)} challenges" for r in rounds)
        did = (f"The Skeptic ran {_plural(len(rounds), 'round')} ({_plural(searches, 'search', 'searches')} of its "
               f"own), found {_plural(new, 'new relevant paper')} and raised {_plural(challenges, 'challenge')}, each "
               "then answered against the evidence." + (f" By round: {per_round}." if rounds else ""))
        return ref, did, [Stat(label="Rounds", value=str(len(rounds))), Stat(label="Challenges", value=str(challenges)),
                          Stat(label="New papers", value=str(new))]
    if key == "value":
        s, fe = verdicts.get("significance"), verdicts.get("feasibility")
        vf = evaluation.value_feasibility or {}
        weeks = (vf.get("feasibility") or {}).get("total_weeks") if isinstance(vf.get("feasibility"), dict) else None
        did = ("Eureka judged significance as " + (_words(s.category) if s else "not yet decided")
               + " and feasibility as " + (_words(fe.category) if fe else "not yet decided")
               + (f"; the plan needs about {weeks} weeks." if weeks else "."))
        return ref, did, [Stat(label="Significance", value=_words(s.category) if s else "–"),
                          Stat(label="Feasibility", value=_words(fe.category) if fe else "–")]
    r = verdicts.get("recommendation")
    did = ("The recommendation is " + (f"{_words(r.category)} (rule {r.rule})" if r else "not decided yet") + ". "
           + " ".join(f"{d.capitalize()}: {_words(verdicts[d].category)}." for d in ("novelty", "significance",
                                                                                     "feasibility") if d in verdicts))
    return ref, did, [Stat(label="Recommendation", value=_words(r.category) if r else "–")]


async def _problems(session: AsyncSession, investigation_id: uuid.UUID) -> tuple[ProblemsOutput, str, list[Stat]]:
    items = list((await session.execute(select(OpportunityItem).where(
        OpportunityItem.investigation_id == investigation_id, OpportunityItem.type == "limitation_cluster")
        .order_by(OpportunityItem.created_at))).scalars())
    quote_ids = [s.get("quote_id") for i in items for s in (i.content or {}).get("sources", [])[:1] if s.get("quote_id")]
    quotes = {str(q.id): q for q in (await session.execute(select(Quote).where(
        Quote.id.in_([uuid.UUID(x) for x in quote_ids])))).scalars()} if quote_ids else {}
    problems = []
    for item in items:
        first = next(iter((item.content or {}).get("sources", [])), {})
        quote = quotes.get(str(first.get("quote_id")))
        problems.append(Problem(label=item.label, papers=int((item.counts or {}).get("papers", 0)),
                                quote=quote.text if quote else None, quote_verified=bool(quote and quote.verified)))
    problems.sort(key=lambda p: -p.papers)
    did = (f"Eureka grouped the limitations and future-work statements of the papers it read into "
           f"{_plural(len(problems), 'open problem')} that several papers share."
           + (f" The best supported, by {_plural(problems[0].papers, 'paper')}: {problems[0].label}." if problems else ""))
    return ProblemsOutput(problems=problems), did, [Stat(label="Open problems", value=str(len(problems)))]


async def _ideas(session: AsyncSession, investigation_id: uuid.UUID, key: str) -> tuple[IdeasOutput, str, list[Stat]]:
    candidates = list((await session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation_id).order_by(Candidate.created_at))).scalars())
    rows: list[IdeaRow] = []
    if key == "ideas":
        rows = [IdeaRow(candidate_id=c.id, title=c.title, research_question=c.research_question, status=c.status,
                        detail=_words(c.origin_strategy) or None) for c in candidates if c.version == 1]
        did = f"The Scout proposed {_plural(len(rows), 'idea')}, each answering an open problem from the landscape."
        return IdeasOutput(kind="ideas", ideas=rows), did, [Stat(label="Ideas", value=str(len(rows)))]
    if key == "prescreen":
        for c in candidates:
            p = c.prescreen or {}
            if not p:
                continue
            quick = (p.get("checks") or {}).get("quick_feasibility") or {}
            time = (p.get("checks") or {}).get("time") or {}
            detail = c.rejection_reason or quick.get("note") or (
                f"needs about {time.get('needs_weeks')} of your {time.get('available_weeks'):g} weeks"
                if time.get("needs_weeks") and time.get("available_weeks") else None)
            rows.append(IdeaRow(candidate_id=c.id, title=c.title, research_question=c.research_question,
                                status=c.status, detail=detail, decision=p.get("decision")))
        chosen = sum(1 for r in rows if r.decision == "light_evaluation")
        dropped = sum(1 for r in rows if r.decision == "screened_out")
        did = (f"Eureka checked {_plural(len(rows), 'idea')} against your limits and against existing work: "
               f"{dropped} dropped, {chosen} chosen for the light evaluation, {len(rows) - chosen - dropped} kept in "
               "reserve (used if a chosen one fails).")
        return IdeasOutput(kind="prescreen", ideas=rows), did, [
            Stat(label="Checked", value=str(len(rows))), Stat(label="Chosen", value=str(chosen)),
            Stat(label="Dropped", value=str(dropped))]
    depth = "light" if key == "light" else "full"
    for c in candidates:
        evaluation = (await session.execute(select(Evaluation).where(
            Evaluation.candidate_id == c.id, Evaluation.depth == depth)
            .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()
        if evaluation is None:
            continue
        verdicts = await _verdicts(session, evaluation)
        r, n = verdicts.get("recommendation"), verdicts.get("novelty")
        rows.append(IdeaRow(candidate_id=c.id, title=c.title, research_question=c.research_question, status=c.status,
                            detail=(f"{_words(r.category)}: novelty {_words(n.category)}" if r and n else None),
                            decision=r.category if r else None, novelty=n.category if n else None))
    if key == "light":
        finalists = sum(1 for r in rows if r.status in ("advanced", "full_evaluating", "finalist", "selected",
                                                       "proposal_ready"))
        did = (f"Eureka light-evaluated {_plural(len(rows), 'idea')}; {_plural(finalists, 'idea')} went on to the full "
               "evaluation.")
        return IdeasOutput(kind="light", ideas=rows), did, [Stat(label="Light evaluations", value=str(len(rows))),
                                                           Stat(label="Went on", value=str(finalists))]
    counts = Counter(r.decision for r in rows if r.decision)
    did = (f"{_plural(len(rows), 'finalist')} had the full evaluation: "
           + ", ".join(f"{n} {_words(k)}" for k, n in counts.items()) + "." if rows else "No full evaluation yet.")
    return IdeasOutput(kind="full", ideas=rows), did, [Stat(label="Finalists", value=str(len(rows)))]


async def step_page(session: AsyncSession, investigation: Investigation, run: str, key: str,
                    candidate: Candidate | None = None) -> StepPage | None:
    """One step's page; None if the mode has no such step. `candidate` (Mode 2): a finalist, for its own evaluation
    steps (FINALIST_KEYS)."""
    f = await facts(session, investigation)
    _, summaries_ = summaries(f, run)
    mode = investigation.mode
    finalist = mode == "discover" and candidate is not None
    keys = list(FINALIST_KEYS) if finalist else [s.key for s in summaries_]
    if key not in keys:
        return None
    if finalist:
        evaluation = await full_evaluation(session, candidate.id)
        state = "done" if evaluation is not None and evaluation.status != "running" else \
            "running" if evaluation is not None else "upcoming"
        name = dict((k, n) for k, _, n in EVALUATE_STEPS)[key]
        summary = StepSummary(key=key, number=keys.index(key) + 2, label=name, name=name, state=state, seconds=None,
                              credits=None)
    else:
        summary = next(s for s in summaries_ if s.key == key)
        candidate = f.own if mode == "evaluate" else None
        evaluation = f.own_eval if mode == "evaluate" else None

    stats: list[Stat] = []
    if key in ("idea", "field"):
        output, did = RefOutput(view="clarification", candidate_id=f.own.id if f.own else None), (
            "Waiting for you: check the reading below, answer what you like, and confirm." if summary.state == "waiting"
            else "Eureka's reading was confirmed." if summary.state == "done" else "Reading your request…")
    elif key in ("search", "screening", "reading"):
        if evaluation is None:
            output, did = (SearchOutput(queries=[], papers=0) if key == "search" else
                           ScreeningOutput(screened=0, relevant=0, selected=0, scores={}, kept=[], dropped=[])
                           if key == "screening" else ReadingOutput(read=0, full_text=0, findings=0, quotes=0,
                                                                    quotes_verified=0, papers=[])), "Not reached yet."
        else:
            output, did, stats = await {"search": _search, "screening": _screening, "reading": _reading}[key](
                session, evaluation)
    elif key in ("novelty", "skeptic", "value", "verdict"):
        output, did, stats = await _evaluation_ref(session, key, evaluation, candidate)
    elif key == "package":
        target = (await session.execute(select(ResearchPackage.candidate_id).join(
            Candidate, Candidate.id == ResearchPackage.candidate_id).where(
            Candidate.investigation_id == investigation.id).order_by(ResearchPackage.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        output = RefOutput(view="package", candidate_id=target or (f.own.id if f.own else None))
        did = ("The research package was written; open it to read all 17 sections." if summary.state == "done" else
               "Writing the research package…" if summary.state == "running" else
               "Not written yet: it's optional. Write it from a finalist when the results are ready.")
    elif key == "landscape":
        landscape = (await session.execute(select(func.count()).select_from(OpportunityItem).where(
            OpportunityItem.investigation_id == investigation.id, OpportunityItem.type == "subtopic"))).scalar_one()
        found = (await session.execute(select(func.count(func.distinct(RetrievalHit.paper_id))).where(
            RetrievalHit.investigation_id == investigation.id, RetrievalHit.evaluation_id.is_(None)))).scalar_one()
        read = (await session.execute(select(func.count(func.distinct(RetrievalHit.paper_id))).where(
            RetrievalHit.investigation_id == investigation.id, RetrievalHit.evaluation_id.is_(None),
            RetrievalHit.selected_for_reading.is_(True)))).scalar_one()
        output = RefOutput(view="landscape", candidate_id=None)
        did = (f"Eureka mapped {_plural(landscape, 'subtopic')}: it found {_plural(found, 'paper')} and read "
               f"{_plural(read, 'paper')} closely.")
        stats = [Stat(label="Subtopics", value=str(landscape)), Stat(label="Papers found", value=f"{found:,}"),
                 Stat(label="Read closely", value=str(read))]
    elif key == "problems":
        output, did, stats = await _problems(session, investigation.id)
    elif key in ("ideas", "prescreen", "light", "full"):
        output, did, stats = await _ideas(session, investigation.id, key)
    else:  # ranking
        output, did = RefOutput(view="ranking", candidate_id=None), (
            "The finalists are ranked below, with why each place and why the other ideas stopped."
            if summary.state == "done" else "Not reached yet.")
    if summary.state == "upcoming" and key not in ("package",):
        did = "Not reached yet."
    stats = [Stat(label="Time", value=_minutes(summary.seconds)) if not finalist else None, *stats,
             Stat(label="Credits", value=str(summary.credits)) if summary.credits is not None else None]
    log = [] if finalist else [LogLine(at=e.created_at, message=e.message) for e, k in f.event_keys if k == key]
    i = keys.index(key)
    return StepPage(step=summary, mode=mode, candidate_id=candidate.id if candidate else None, does=DOES[key], did=did,
                    stats=[s for s in stats if s is not None], log=log, output=output,
                    previous=keys[i - 1] if i > 0 else None, next=keys[i + 1] if i < len(keys) - 1 else None)


__all__ = ["DOES", "EVALUATE_STEPS", "DISCOVER_STEPS", "FINALIST_KEYS", "RunFacts", "current_key", "facts",
           "step_page", "steps_overview", "summaries"]
