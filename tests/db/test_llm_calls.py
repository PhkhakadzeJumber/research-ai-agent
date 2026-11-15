"""Tests for storing Claude call records (llm_calls) against the real test database (rolled back)."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.db.models import Investigation, LlmCall
from src.db.repositories.llm_calls import record_llm_calls
from src.db.repositories.runs import create_investigation
from src.llm import LlmCallRecord

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def call(attempt=1, cost=Decimal("0.031200"), **overrides):
    values = dict(
        role="paper_reader", model="claude-opus-5-5", effort="low",
        prompt_version="shared_preamble@1+paper_reader@1", attempt=attempt, input_tokens=800, output_tokens=500,
        thinking_tokens=100, cache_read_tokens=2200, cache_creation_tokens=0, latency_ms=4200, stop_reason="STOP",
        cost_usd=cost,
    )
    values.update(overrides)
    return LlmCallRecord(**values)


async def test_calls_are_stored_and_added_to_the_investigation_cost(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"i": 1})
    await record_llm_calls(db_session, [call(1), call(2, cost=Decimal("0.020000"))],
                           investigation_id=investigation.id)

    rows = (await db_session.execute(select(LlmCall).order_by(LlmCall.attempt))).scalars().all()
    assert [(row.attempt, row.cost_usd) for row in rows] == [(1, Decimal("0.031200")), (2, Decimal("0.020000"))]
    assert rows[0].prompt_version == "shared_preamble@1+paper_reader@1"
    await db_session.refresh(investigation)
    assert investigation.cost_usd == Decimal("0.0512")


async def test_calls_outside_an_investigation_and_unknown_costs(db_session):
    await record_llm_calls(db_session, [call(cost=None)])  # e.g. a library paper read with an unpriced model
    row = (await db_session.execute(select(LlmCall))).scalar_one()
    assert row.investigation_id is None and row.cost_usd is None


async def test_negative_tokens_are_rejected(db_session):
    with pytest.raises(IntegrityError, match="ck_llm_calls_tokens_not_negative"):
        async with db_session.begin_nested():
            await record_llm_calls(db_session, [call(input_tokens=-1)])


async def test_deleting_an_investigation_deletes_its_calls(db_session):
    from sqlalchemy import delete, func

    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"i": 1})
    await record_llm_calls(db_session, [call()], investigation_id=investigation.id)
    await db_session.execute(delete(Investigation).where(Investigation.id == investigation.id))
    assert (await db_session.execute(select(func.count()).select_from(LlmCall))).scalar() == 0
