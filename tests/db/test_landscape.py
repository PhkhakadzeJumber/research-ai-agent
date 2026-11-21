"""Tests for the landscape search (src/ai/workflows/landscape.py) and its part of scripts/discover.py.
Real test database (rolled back), fake Claude and fake sources."""

import pytest
from sqlalchemy import select

from scripts.discover import landscape
from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.workflows.field import confirm_brief, load_pending_brief
from src.ai.workflows.landscape import KINDS, landscape_step, plan_landscape
from src.ai.workflows.transitions import StaleState
from src.db.models import Investigation, RetrievalHit, SearchQuery
from tests.ai.test_field_brief import VALID
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import FakeSearch
from tests.db.test_field_workflow import understood

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def confirmed(db_session):
    """A Mode 2 investigation at landscape_retrieval (the 4-subtopic brief, 2 searches each)."""
    investigation = await understood(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending_brief(sessions, investigation.id)
    await confirm_brief(sessions, investigation.id, final=pending.brief)
    return investigation


class Sources:
    """search_for(sources) for tests: one FakeSearch per source combination, sharing one table."""

    def __init__(self, table=None, *, fail=()):
        self.table, self.fail, self.searches = table or {}, set(fail), {}

    def __call__(self, sources):
        key = tuple(sources)
        if key not in self.searches:
            self.searches[key] = FakeSearch(self.table, fail=self.fail & set(key), sources=key)
        return self.searches[key]


def test_every_search_is_run_four_ways():
    brief = FieldBriefOutput.model_validate(VALID)
    planned = plan_landscape(brief, year=2026)
    assert len(planned) == 4 * 2 * len(KINDS)  # subtopics x searches x kinds
    first = [p for p in planned if p.request.query.startswith("LLM agent evaluation a")]
    by_kind = {p.kind.name: p for p in first}
    assert (by_kind["recent"].request.year_from, by_kind["recent"].kind.sources) == (2024, ("openalex", "arxiv"))
    assert by_kind["survey"].request.query == "LLM agent evaluation a survey"
    cited = by_kind["cited"].request
    assert (cited.sort, cited.min_citations, cited.year_from) == ("relevance", 50, 2022)  # well cited AND relevant
    assert (by_kind["newest"].request.sort, by_kind["newest"].kind.sources) == ("newest", ("arxiv",))


async def test_the_landscape_is_stored_and_the_stage_moves_on(db_session):
    investigation = await confirmed(db_session)
    sources = Sources({"LLM agent evaluation a": ["Agent bench", "Tool use study"],
                       "LLM agent evaluation a survey": ["A survey of agents"],
                       "multilingual tool use a": ["Tool use study"]})  # found again: one paper
    outcome = await landscape_step(sessions_on(db_session), investigation.id, sources)

    assert outcome.searches == 32 and not outcome.failed
    assert outcome.unique_papers == 3
    assert outcome.by_subtopic["LLM agent evaluation"] == 3 and outcome.by_subtopic["multilingual tool use"] == 1
    queries = (await db_session.execute(select(SearchQuery).where(SearchQuery.investigation_id == investigation.id))
               ).scalars().all()
    assert {q.origin for q in queries} == {"landscape"}
    assert {q.query_type for q in queries} == {"recent", "survey", "cited", "newest"}
    assert {q.source for q in queries if q.query_type == "newest"} == {"arxiv"}
    hits = (await db_session.execute(select(RetrievalHit).where(RetrievalHit.investigation_id == investigation.id))
            ).scalars().all()
    assert len(hits) == 3 and all(hit.evaluation_id is None for hit in hits)  # the landscape scope
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("landscape_reading", "running")


async def test_requests_carry_their_kind(db_session):
    investigation = await confirmed(db_session)
    sources = Sources()
    await landscape_step(sessions_on(db_session), investigation.id, sources)
    cited = [r for r in sources(("openalex",)).requests if r.min_citations == 50]
    newest = sources(("arxiv",)).requests
    assert len(cited) == 8 and all(r.year_from is not None for r in cited)
    assert len(newest) == 8 and all(r.sort == "newest" and r.year_from is None for r in newest)


async def test_if_nothing_could_be_searched_the_investigation_fails(db_session):
    investigation = await confirmed(db_session)
    outcome = await landscape_step(sessions_on(db_session), investigation.id, Sources(fail=("openalex", "arxiv")))
    assert outcome.failed
    await db_session.refresh(investigation)
    assert investigation.status == "failed"


async def test_the_landscape_needs_a_confirmed_brief(db_session):
    investigation = await understood(db_session)  # still awaiting clarification
    with pytest.raises(StaleState):
        await landscape_step(sessions_on(db_session), investigation.id, Sources())


async def test_the_command_shows_the_landscape(db_session, capsys):
    investigation = await confirmed(db_session)
    code = await landscape(sessions_on(db_session), investigation.id,
                           search_for=Sources({"LLM agent evaluation a": ["Agent bench"]}))
    output = capsys.readouterr().out
    assert code == 0
    assert "32 searches -> 3 results" in output and "1 unique papers" in output  # recent, cited, newest: one paper
    assert "Papers per subtopic:" in output  # the next-step note moved to reading (test_landscape_reading)
    assert (await db_session.get(Investigation, investigation.id)).stage == "landscape_reading"
