"""Starting and controlling runs (doc 11 §3; step 9b), and exporting reports.

Each route checks the request against the stored state first (a clear 4xx, nothing stored or paid for), reserves
the run's credits (src/api/billing.py: the plan, the free evaluation, enough credits; a 402/403 otherwise), commits
what it changes, then hands the paid work to a background run (src/api/runs.py) and answers 202 at once with the
investigation (run: "running"). The client follows the stage and cost with GET /investigations/{id} (live progress
events come in step 9c).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.reporting import build_report, package_markdown
from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.schemas.structured_idea import StructuredIdeaOutput
from src.ai.workflows.budget import set_light_cap
from src.ai.workflows.clarification import apply_edits
from src.ai.workflows.driver import STAGES
from src.ai.workflows.field import remove_subtopics
from src.ai.workflows.proposal import package_sections
from src.ai.workflows.states import FINAL_INVESTIGATION_STATUSES, CandidateStatus, Stage
from src.ai.workflows.user_document import DocumentError, attach_document, prepare_document_bytes
from src.api import billing
from src.api.auth import CurrentUser
from src.api.deps import RunsDep, SessionDep
from src.api.errors import ApiError, not_found
from src.api.routes.candidates import get_candidate
from src.api.routes.investigations import get_investigation, summary_of
from src.api.runs import Clarified, RunConflict, Runs
from src.api.schemas.accounts import profile_constraints
from src.api.schemas.common import ERRORS
from src.api.schemas.investigations import InvestigationSummary
from src.api.schemas.requests import (
    ClarificationAnswer,
    Complexity,
    Constraints,
    DiscoverRequest,
    NewInvestigation,
    ResumeRequest,
)
from src.config import get_api_settings
from src.db.models import CreditHold, Investigation, ResearchPackage, User
from src.db.repositories.intakes import find_intake
from src.db.repositories.runs import create_investigation
from src.timeutil import now

router = APIRouter(tags=["runs"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # a 50-page PDF is far below this; the text limit is checked after reading
FINALISTS = (CandidateStatus.FINALIST, CandidateStatus.SELECTED, CandidateStatus.PROPOSAL_READY)
RUN_ERRORS = {**ERRORS,
              402: {"model": ERRORS[404]["model"], "description": "Not enough credits"},
              403: {"model": ERRORS[404]["model"], "description": "Not in your plan, or this month's free "
                                                                 "evaluation is used"},
              409: {"model": ERRORS[404]["model"], "description": "Not possible in this state"},
              503: {"model": ERRORS[404]["model"], "description": "Free evaluations are paused until next month"}}


def checked_budget(budget: float | None) -> float | None:
    """A budget the request asked for: it can only lower the run's cost cap (the plan sets it, src/api/billing.py)."""
    ceiling = get_api_settings().max_budget_usd
    if budget is not None and budget > ceiling:
        raise ApiError(422, "budget_too_high", f"The budget can be at most ${ceiling:g} per investigation.")
    return budget


def limits_for(user: User, typed: dict[str, str]) -> dict[str, str]:
    """The run's limits: the user's profile, with what they typed for this run taking precedence (step 9d)."""
    return {**profile_constraints(user.profile), **dict(typed)}


async def launch(session: AsyncSession, runs: Runs, investigation_id: uuid.UUID, hold: CreditHold, what: str,
                 work) -> None:
    """Saves the reservation (and anything else the route changed: the run uses its own session), then starts the
    run; if another run started first, the reservation is undone."""
    await session.commit()
    try:
        runs.start(investigation_id, what, work)
    except RunConflict as error:
        await billing.release(session, hold)
        await session.commit()
        raise ApiError(409, "already_running", str(error)) from None


async def started(session: AsyncSession, investigation: Investigation, runs: Runs) -> InvestigationSummary:
    return InvestigationSummary(**await summary_of(session, investigation, runs))


# --- new investigations -----------------------------------------------------------------------------------

@router.post("/investigations", status_code=202, response_model=InvestigationSummary, responses=RUN_ERRORS)
async def create(body: NewInvestigation, session: SessionDep, user: CurrentUser, runs: RunsDep) -> InvestigationSummary:
    """Starts a new investigation: Eureka's first reading of the idea or field runs in the background (a few
    cents), then the investigation waits for the clarification."""
    budget = checked_budget(body.budget_usd)
    if isinstance(body, DiscoverRequest):
        data = {"field": body.field, "count": body.count}
        data.update({key: value for key, value in (("interests", body.interests), ("technologies", body.technologies),
                                                   ("domain", body.domain)) if value})
        if body.exclusions:
            data["exclusions"] = list(body.exclusions)
    else:
        data = {"idea": body.idea}
        if body.field:
            data["field"] = body.field
        if body.seed_papers:
            data["seed_papers"] = list(body.seed_papers)
    investigation = await create_investigation(session, mode=body.mode, complexity=body.complexity, input=data,
                                               user_id=user.id, constraints=limits_for(user, body.constraints))
    hold = await billing.reserve(session, user, investigation, body.mode, budget_usd=budget)
    if isinstance(body, DiscoverRequest) and body.max_light is not None:
        set_light_cap(investigation, body.max_light)
    await launch(session, runs, investigation.id, hold, "understanding", lambda: runs.understand(investigation.id))
    return await started(session, investigation, runs)


@router.post("/investigations/document", status_code=202, response_model=InvestigationSummary,
             responses={**RUN_ERRORS, 413: {"model": ERRORS[404]["model"], "description": "File too large"}})
async def create_from_document(
    session: SessionDep, user: CurrentUser,
    runs: RunsDep,
    file: Annotated[UploadFile, File(description="Your draft or proposal: .pdf, .md or .txt")],
    consent: Annotated[bool, Form(description="You agree to send the document's text to Claude to read it")],
    published: Annotated[bool | None, Form()] = None,
    doi: Annotated[str | None, Form(max_length=200)] = None,
    arxiv: Annotated[str | None, Form(max_length=50)] = None,
    field: Annotated[str | None, Form(max_length=300)] = None,
    complexity: Annotated[Complexity, Form()] = "beginner",
    constraints: Annotated[str | None, Form(description='JSON object, e.g. {"available time": "8 weeks"}')] = None,
    budget_usd: Annotated[float | None, Form(gt=0)] = None,
) -> InvestigationSummary:
    """Mode 1 from the user's own document. Without consent nothing is stored. The document stays private to this
    investigation: never added to the shared library or sent to search engines."""
    if not consent:
        raise ApiError(422, "consent_required", "Eureka needs your consent to send the document to Claude; "
                                                "nothing was stored.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "file_too_large", f"The file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        prepared = prepare_document_bytes(file.filename or "document.txt", data)
    except DocumentError as error:
        raise ApiError(422, "invalid_document", str(error)) from None
    try:
        limits = TypeAdapter(Constraints).validate_python(json.loads(constraints)) if constraints else {}
    except (ValueError, ValidationError):
        raise ApiError(422, "invalid_request", 'constraints: a JSON object of short texts, e.g. '
                                               '{"available time": "8 weeks"}') from None
    budget = checked_budget(budget_usd)
    request = {"idea": ""} | ({"field": field.strip()} if field and field.strip() else {})
    investigation = await create_investigation(session, mode="evaluate", complexity=complexity, input=request,
                                               user_id=user.id, constraints=limits_for(user, limits))
    hold = await billing.reserve(session, user, investigation, "evaluate", budget_usd=budget)
    ids = {key: value.strip() for key, value in (("doi", doi), ("arxiv", arxiv)) if value and value.strip()}
    await attach_document(session, investigation.id, prepared, consent_at=now(),
                          published=bool(published or ids) or None, external_ids=ids)
    await launch(session, runs, investigation.id, hold, "understanding", lambda: runs.understand(investigation.id))
    return await started(session, investigation, runs)


# --- the clarification ------------------------------------------------------------------------------------

@router.post("/investigations/{investigation_id}/clarification", status_code=202,
             response_model=InvestigationSummary, responses=RUN_ERRORS)
async def answer_clarification(investigation_id: uuid.UUID, body: ClarificationAnswer, session: SessionDep,
                               user: CurrentUser, runs: RunsDep) -> InvestigationSummary:
    """The user's answers, edits and confirmation. Answers are applied by one Idea Analyst call (only if any were
    given); then the evaluation (Mode 1) or the discovery (Mode 2) runs in the background."""
    investigation = await get_investigation(session, investigation_id, user)
    intake = await find_intake(session, investigation_id)
    if investigation.stage != Stage.AWAITING_CLARIFICATION or intake is None or intake.confirmed_at is not None:
        raise ApiError(409, "not_waiting", "This investigation isn't waiting for a clarification.")
    if runs.state(investigation_id) != "idle":
        raise ApiError(409, "already_running", "Eureka is still working on this investigation.")

    discover = investigation.mode == "discover"
    reading = (FieldBriefOutput if discover else StructuredIdeaOutput).model_validate(intake.agent_reading)
    questions = len(reading.clarifying_questions)
    if len(body.answers) > questions:
        raise ApiError(422, "invalid_request", f"There are {questions} questions; got {len(body.answers)} answers.")
    if discover and body.edits:
        raise ApiError(422, "invalid_request", "edits are for an idea's facets (Mode 1); a field brief takes "
                                               "remove_subtopics.")
    if not discover and body.remove_subtopics:
        raise ApiError(422, "invalid_request", "remove_subtopics is for a field brief (Mode 2).")
    try:  # check the changes against the reading now, so a mistake is a 422 and not a failed run
        if body.edits:
            apply_edits(reading, body.edits)
        if body.remove_subtopics:
            remove_subtopics(reading, body.remove_subtopics)
    except ValueError as error:
        raise ApiError(422, "invalid_change", str(error).splitlines()[0]) from None

    replies = [*body.answers, *([None] * (questions - len(body.answers)))]
    clarified = Clarified(replies=replies, edits=dict(body.edits), remove_subtopics=list(body.remove_subtopics))
    hold = await billing.reserve(session, user, investigation, investigation.mode, continuing=True)
    await launch(session, runs, investigation_id, hold, "clarifying",
                 lambda: runs.clarify(investigation_id, investigation.mode, clarified))
    return await started(session, investigation, runs)


# --- stop and resume --------------------------------------------------------------------------------------

@router.post("/investigations/{investigation_id}/stop", status_code=202, response_model=InvestigationSummary,
             responses=RUN_ERRORS)
async def stop(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser,
               runs: RunsDep) -> InvestigationSummary:
    """Stops the run at the next step boundary: nothing is lost, and it can be resumed."""
    investigation = await get_investigation(session, investigation_id, user)
    if not runs.stop(investigation_id):
        raise ApiError(409, "not_running", "Nothing is running for this investigation.")
    return await started(session, investigation, runs)


@router.post("/investigations/{investigation_id}/resume", status_code=202, response_model=InvestigationSummary,
             responses=RUN_ERRORS)
async def resume(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser, runs: RunsDep,
                 body: ResumeRequest | None = None) -> InvestigationSummary:
    """Continues where it stopped: after Stop, the budget running out, an account refusal or a server restart."""
    investigation = await get_investigation(session, investigation_id, user)
    if runs.state(investigation_id) != "idle":
        raise ApiError(409, "already_running", "Eureka is already working on this investigation.")
    if investigation.status in FINAL_INVESTIGATION_STATUSES:
        raise ApiError(409, "finished", f"This investigation {investigation.status}; start a new one.")
    stage = investigation.stage
    if stage == Stage.AWAITING_CLARIFICATION:
        raise ApiError(409, "waiting_for_clarification", "Answer the clarification first.")
    if stage == Stage.INTAKE:
        work, what = (lambda: runs.understand(investigation_id)), "understanding"
    elif stage in STAGES:
        work, what = (lambda: runs.drive(investigation_id)), "running"
    else:
        raise ApiError(409, "nothing_to_resume", f"Nothing to resume at stage {stage.replace('_', ' ')}.")
    budget = checked_budget(body.budget_usd) if body is not None else None
    hold = await billing.reserve(session, user, investigation, investigation.mode, continuing=True,
                                 budget_usd=budget)
    await launch(session, runs, investigation_id, hold, what, work)
    return await started(session, investigation, runs)


# --- the research package ---------------------------------------------------------------------------------

@router.post("/candidates/{candidate_id}/proposal", status_code=202, response_model=InvestigationSummary,
             responses=RUN_ERRORS)
async def write_package(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser,
                        runs: RunsDep) -> InvestigationSummary:
    """Writes the research package for a finalist in the background (the Proposal Designer, one call, about 35
    cents). A second call writes a new revision."""
    candidate = await get_candidate(session, candidate_id, user)
    investigation = await get_investigation(session, candidate.investigation_id, user)
    if candidate.status not in FINALISTS:
        raise ApiError(409, "not_a_finalist", f"A research package is written for a finalist; this idea is "
                                              f"{candidate.status.replace('_', ' ')}.")
    if investigation.stage not in (Stage.RESULTS_READY, Stage.WRITING_PROPOSAL):
        raise ApiError(409, "no_results_yet", "The investigation's results aren't ready yet.")
    if runs.state(investigation.id) != "idle":
        raise ApiError(409, "already_running", "Eureka is still working on this investigation.")
    hold = await billing.reserve(session, user, investigation, "package", candidate_id=candidate_id)
    await launch(session, runs, investigation.id, hold, "writing the research package",
                 lambda: runs.write_package(investigation.id, candidate_id))
    return await started(session, investigation, runs)


# --- exports ----------------------------------------------------------------------------------------------

def _markdown(text: str, name: str) -> Response:
    return Response(text + "\n", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@router.get("/investigations/{investigation_id}/report", response_class=Response, responses=ERRORS)
async def export_report(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser,
                        format: Literal["md"] = "md") -> Response:
    """The full report as Markdown (PDF later), built from stored data: free."""
    await get_investigation(session, investigation_id, user)
    return _markdown(await build_report(session, investigation_id), f"eureka-report-{investigation_id}")


@router.get("/candidates/{candidate_id}/proposal/export", response_class=Response, responses=ERRORS)
async def export_package(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser,
                         format: Literal["md"] = "md") -> Response:
    """The latest research package as Markdown (PDF later)."""
    candidate = await get_candidate(session, candidate_id, user)
    package = (await session.execute(select(ResearchPackage).where(ResearchPackage.candidate_id == candidate_id)
                                     .order_by(ResearchPackage.revision.desc()).limit(1))).scalar_one_or_none()
    if package is None:
        raise not_found("Research package")
    text = package_markdown(candidate, package, await package_sections(session, package))
    return _markdown(text, f"eureka-package-{candidate_id}-r{package.revision}")
