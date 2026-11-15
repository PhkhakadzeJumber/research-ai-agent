"""Tests for storing readings and read_into_library() against the real test database (rolled back).
Claude is replaced by fake ADK models: no network, no cost."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.ai.agents import paper_reader
from src.ai.workflows import reading as reading_workflow
from src.ai.workflows.reading import read_into_library
from src.config import RoleLlmSettings
from src.db.models import Chunk, Investigation, LlmCall, Paper, PaperAnalysis, Quote
from src.db.repositories.runs import create_investigation
from tests.fakes import PaperAwareLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

SETTINGS = RoleLlmSettings(role="paper_reader", model="claude-opus-5-5", effort="low", max_tokens=16000)
ABSTRACT = ("We study retrieval-augmented generation for low-resource languages. Retrieval reduces "
            "hallucinations of small language models on the AfriQA benchmark.")
GOOD_QUOTE = "Retrieval reduces hallucinations of small language models"


def output(*quotes, datasets=("AfriQA",)):
    """A Paper Reader answer whose limitations carry the given (text, chunk_id) quotes."""
    return answer(json.dumps({
        "contribution_type": "empirical_study", "datasets": list(datasets),
        "limitations": [{"statement": "s", "quote": {"text": text, "chunk_id": chunk}} for text, chunk in quotes],
    }))


def sessions_on(db_session):
    @asynccontextmanager
    async def sessions():
        yield db_session
        await db_session.flush()

    return sessions


async def add_paper(session, *, abstract=ABSTRACT, arxiv="2401.00001"):
    paper = Paper(title=f"Paper {arxiv}", abstract=abstract, tier=2, arxiv_id=arxiv)
    session.add(paper)
    await session.flush()
    return paper


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def test_reading_is_stored_with_quote_ids_and_verification(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"i": 1})
    paper = await add_paper(db_session)
    llm = PaperAwareLlm.with_answers({str(paper.id): output((GOOD_QUOTE, None), ("an invented sentence", None))})

    [outcome] = await read_into_library(sessions_on(db_session), [paper.id], investigation_id=investigation.id,
                                        settings=SETTINGS, model=llm)

    assert (outcome.reused, outcome.error, outcome.verified_quotes, outcome.quotes) == (False, None, 1, 2)
    analysis = await db_session.get(PaperAnalysis, outcome.analysis_id)
    assert (analysis.extractor_version, analysis.source_level) == (paper_reader.extractor_version(), "abstract")
    first, second = analysis.content["limitations"]
    assert first["quote"]["verified"] is True and first["quote"]["reason"] is None
    assert second["quote"]["verified"] is False and "not found" in second["quote"]["reason"]
    assert analysis.content["verification"] == {"quotes": 2, "verified_quotes": 1, "missing_names": []}

    quote = await db_session.get(Quote, uuid.UUID(first["quote"]["quote_id"]))
    assert (quote.verified, quote.verification_score, quote.source_level) == (True, 1.0, "abstract")
    invented = await db_session.get(Quote, uuid.UUID(second["quote"]["quote_id"]))
    assert invented.verified is False  # kept for debugging, never counted

    [call] = (await db_session.execute(select(LlmCall))).scalars().all()
    assert call.investigation_id == investigation.id
    await db_session.refresh(investigation)
    assert investigation.cost_usd > Decimal(0)


async def test_a_stored_reading_is_reused_without_calling_claude(db_session):
    paper = await add_paper(db_session)
    first_llm = PaperAwareLlm.with_answers({str(paper.id): output((GOOD_QUOTE, None))})
    await read_into_library(sessions_on(db_session), [paper.id], settings=SETTINGS, model=first_llm)

    second_llm = PaperAwareLlm.with_answers({})
    [outcome] = await read_into_library(sessions_on(db_session), [paper.id], settings=SETTINGS, model=second_llm)

    assert outcome.reused and outcome.verified_quotes == 1
    assert second_llm.seen_paper_ids == []  # never paid for twice
    assert await count(db_session, PaperAnalysis) == 1


async def test_a_new_extractor_version_reads_again(db_session, monkeypatch):
    paper = await add_paper(db_session)
    llm = PaperAwareLlm.with_answers({str(paper.id): output((GOOD_QUOTE, None))})
    await read_into_library(sessions_on(db_session), [paper.id], settings=SETTINGS, model=llm)

    monkeypatch.setattr(reading_workflow, "extractor_version", lambda: "reader-p1.2-s1")
    monkeypatch.setattr(paper_reader, "extractor_version", lambda: "reader-p1.2-s1")
    [outcome] = await read_into_library(sessions_on(db_session), [paper.id], settings=SETTINGS, model=llm)

    assert not outcome.reused
    assert await count(db_session, PaperAnalysis) == 2  # old and new versions side by side
    assert await count(db_session, Quote) == 1  # the same quote is one global row


async def test_full_text_quotes_point_at_their_chunk(db_session):
    paper = await add_paper(db_session)
    chunk = Chunk(paper_id=paper.id, chunker_version="v1", position=0, section_title="Limitations",
                  text="We only evaluate models with at least seven billion parameters.")
    db_session.add(chunk)
    await db_session.flush()
    llm = PaperAwareLlm.with_answers({str(paper.id): output(
        ("We only evaluate models with at least seven billion parameters", str(chunk.id)),
        ("Retrieval helps", str(uuid.uuid4())),  # a chunk that doesn't exist
    )})

    [outcome] = await read_into_library(sessions_on(db_session), [paper.id], settings=SETTINGS, model=llm)

    analysis = await db_session.get(PaperAnalysis, outcome.analysis_id)
    assert analysis.source_level == "full_text"
    good, missing = analysis.content["limitations"]
    stored = await db_session.get(Quote, uuid.UUID(good["quote"]["quote_id"]))
    assert (stored.chunk_id, stored.source_level, stored.verified) == (chunk.id, "full_text", True)
    assert missing["quote"]["quote_id"] is None and missing["quote"]["verified"] is False
    assert await count(db_session, Quote) == 1


async def test_failures_and_unknown_papers_do_not_stop_the_batch(db_session):
    good = await add_paper(db_session, arxiv="2401.00001")
    no_text = await add_paper(db_session, abstract=None, arxiv="2401.00002")
    unknown = uuid.uuid4()
    llm = PaperAwareLlm.with_answers({str(good.id): output((GOOD_QUOTE, None))})

    outcomes = await read_into_library(sessions_on(db_session), [good.id, no_text.id, unknown, good.id],
                                       settings=SETTINGS, model=llm)

    assert [o.paper_id for o in outcomes] == [good.id, no_text.id, unknown, good.id]  # input order, duplicates kept
    assert outcomes[0].analysis_id is not None and outcomes[3].analysis_id == outcomes[0].analysis_id
    assert "neither an abstract nor full-text chunks" in outcomes[1].error
    assert outcomes[2].error == "paper not found in the library"
    assert llm.seen_paper_ids == [str(good.id)]  # read once, though listed twice


async def test_concurrent_requests_for_the_same_paper_call_claude_once(db_session):
    paper = await add_paper(db_session)
    llm = PaperAwareLlm.with_answers({str(paper.id): output((GOOD_QUOTE, None))}, delay=0.2)
    sessions = sessions_on(db_session)

    first, second = await asyncio.gather(
        read_into_library(sessions, [paper.id], settings=SETTINGS, model=llm),
        read_into_library(sessions, [paper.id], settings=SETTINGS, model=llm),
    )

    assert llm.seen_paper_ids == [str(paper.id)]  # single-flight: one Claude call
    assert first[0].analysis_id == second[0].analysis_id
    assert await count(db_session, PaperAnalysis) == 1


async def test_cost_lands_on_the_investigation_only_once(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"i": 1})
    paper = await add_paper(db_session)
    llm = PaperAwareLlm.with_answers({str(paper.id): output((GOOD_QUOTE, None))})
    await read_into_library(sessions_on(db_session), [paper.id], investigation_id=investigation.id,
                            settings=SETTINGS, model=llm)
    await db_session.refresh(investigation)
    cost_after_first = investigation.cost_usd

    await read_into_library(sessions_on(db_session), [paper.id], investigation_id=investigation.id,
                            settings=SETTINGS, model=llm)  # reused: free
    await db_session.refresh(investigation)
    assert investigation.cost_usd == cost_after_first
    assert (await db_session.get(Investigation, investigation.id)) is not None
