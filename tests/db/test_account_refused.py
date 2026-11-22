"""Tests for account-level refusals (src/llm.py LlmAccountError; src/ai/workflows/runner.py): when Anthropic refuses
the account itself (usage limit, credit, key), a run stops where it is and resumes later, instead of carrying on
"degraded" and concluding with no evidence (what the step 6a.2 live check did). Real test database, fake Claude."""

import httpx
import pytest
from google.adk.models.base_llm import BaseLlm
from sqlalchemy import select

import anthropic
from src.ai.workflows.light_evaluation import light_evaluation_step
from src.config import RoleLlmSettings
from src.db.models import Candidate, Evaluation, Verdict
from src.llm import LlmAccountError, LlmError, is_account_error, run_structured
from src.ai.schemas.screening import ScreeningOutput
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_light_evaluation import fake_models, prescreened, search

pytestmark = [pytest.mark.anyio]

USAGE_LIMIT = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
               "'You have reached your specified workspace API usage limits. You will regain access on 2026-12-01.'}}")


def api_error(cls, message, status):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=None)


class Refusing(BaseLlm):
    """A fake model whose every call is refused by the API with `error`."""

    model: str = "claude-haiku-5-5"
    error: object = None

    async def generate_content_async(self, llm_request, stream=False):
        raise self.error
        yield  # pragma: no cover  (makes this an async generator)


def test_account_refusals_are_told_apart_from_request_errors():
    assert is_account_error(api_error(anthropic.BadRequestError, USAGE_LIMIT, 400))
    assert is_account_error(api_error(anthropic.BadRequestError, "Your credit balance is too low", 400))
    assert is_account_error(api_error(anthropic.AuthenticationError, "invalid x-api-key", 401))
    assert not is_account_error(api_error(anthropic.BadRequestError, "prompt is too long", 400))
    assert not is_account_error(api_error(anthropic.InternalServerError, "overloaded", 500))


async def test_run_structured_raises_the_account_error():
    settings = RoleLlmSettings(role="relevance_screening", model="claude-haiku-5-5", effort="low", max_tokens=1000)
    refusing = Refusing(error=api_error(anthropic.BadRequestError, USAGE_LIMIT, 400))
    with pytest.raises(LlmAccountError, match="refused the account"):
        await run_structured("relevance_screening", system="s", prompt_version="v", user_message="u",
                             output_schema=ScreeningOutput, settings=settings, model=refusing)
    ordinary = Refusing(error=api_error(anthropic.BadRequestError, "prompt is too long", 400))
    with pytest.raises(LlmError) as caught:
        await run_structured("relevance_screening", system="s", prompt_version="v", user_message="u",
                             output_schema=ScreeningOutput, settings=settings, model=ordinary)
    assert not isinstance(caught.value, LlmAccountError)


@pytest.mark.db
async def test_a_refused_account_stops_the_evaluation_where_it_is_and_it_resumes(db_session):
    investigation = await prescreened(db_session)
    refused = fake_models()
    refused.screen_model = Refusing(error=api_error(anthropic.BadRequestError, USAGE_LIMIT, 400))

    outcome = await light_evaluation_step(sessions_on(db_session), investigation.id, models=refused,
                                          search_factory=search)
    assert (outcome.stopped, outcome.stopped_at) == ("account_refused", "Judge calibration Georgian: screening")
    evaluation = (await db_session.execute(select(Evaluation).join(Candidate).where(
        Candidate.investigation_id == investigation.id))).scalars().one()
    await db_session.refresh(evaluation)
    assert (evaluation.step, evaluation.status) == ("screening", "running")  # not degraded, not concluded
    assert (await db_session.execute(select(Verdict).where(Verdict.evaluation_id == evaluation.id))).first() is None

    # access is back: the same evaluation resumes at screening and finishes
    outcome = await light_evaluation_step(sessions_on(db_session), investigation.id, models=fake_models(),
                                          search_factory=search)
    assert outcome.stopped is None and len(outcome.triaged) == 2
