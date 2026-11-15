"""Tests for storing embeddings and ranking a scope, against the real test database (rolled back).

Uses FakeEmbedder: real vector maths in Postgres (pgvector), no Google account needed.
"""

import uuid

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError

from src.db.models import Paper
from src.db.repositories.embeddings import embed_papers, paper_text
from src.db.repositories.ranking import rank_scope
from src.db.repositories.runs import create_investigation
from src.db.repositories.search_trail import save_search
from src.retrieval.embeddings import EMBEDDING_DIMENSIONS, FakeEmbedder
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, SearchResult, SourceReport

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def paper(openalex_id, title, abstract=None, **extra):
    return Paper(title=title, abstract=abstract, tier=1, openalex_id=openalex_id, **extra)


async def add(session, *objects):
    session.add_all(objects)
    await session.flush()


# --- storing embeddings -----------------------------------------------------------------------------

async def test_papers_are_embedded_once_per_model(db_session):
    embedder = FakeEmbedder()
    first, second = paper("W1", "Small language models"), paper("W2", "Graph neural networks", "An abstract.")
    await add(db_session, first, second)

    assert await embed_papers(db_session, embedder, [first, second]) == 2
    await db_session.refresh(first)
    assert len(first.embedding) == EMBEDDING_DIMENSIONS
    assert first.embedding_model == embedder.model_id

    assert await embed_papers(db_session, embedder, [first, second]) == 0  # already embedded: nothing paid twice
    assert embedder.calls == [("RETRIEVAL_DOCUMENT", 2)]


async def test_a_different_model_re_embeds(db_session):
    stored = paper("W1", "Small language models")
    await add(db_session, stored)
    await embed_papers(db_session, FakeEmbedder(), [stored])

    class OtherModel(FakeEmbedder):
        model_id = "another-model"

    assert await embed_papers(db_session, OtherModel(), [stored]) == 1
    assert stored.embedding_model == "another-model"


def test_paper_text_is_title_plus_abstract():
    assert paper_text(paper("W1", "Title", "Abstract.")) == "Title\n\nAbstract."
    assert paper_text(paper("W1", "Title only")) == "Title only"


async def test_vector_without_model_is_rejected(db_session):
    # The CHECK constraint added by hand in migration 0004 (autogenerate can't detect it).
    with pytest.raises(IntegrityError, match="ck_papers_embedding_has_model"):
        async with db_session.begin_nested():
            await add(db_session, paper("W1", "A paper", embedding=[0.1] * EMBEDDING_DIMENSIONS))


async def test_vectors_of_the_wrong_size_are_rejected(db_session):
    with pytest.raises((DBAPIError, StatementError), match="expected 768 dimensions"):
        async with db_session.begin_nested():
            await add(db_session, paper("W1", "A paper", embedding=[0.1, 0.2, 0.3], embedding_model="m"))


# --- ranking a scope -------------------------------------------------------------------------------------

def record(openalex_id, title, **extra):
    return PaperRecord(ids=ExternalIds(openalex_id=openalex_id), title=title, sources=("openalex",), tier=1,
                       authors=(f"Author {openalex_id}",), **extra)


async def saved_scope(session, *records, evaluation_id=None):
    investigation = await create_investigation(session, mode="discover", complexity="beginner", input={"q": "x"})
    result = SearchResult(request=SearchRequest(query="x"), papers=tuple(records),
                          reports=(SourceReport(source="openalex", ok=True, result_count=len(records)),),
                          retrieved_count=len(records))
    await save_search(session, investigation.id, result, origin="landscape", evaluation_id=evaluation_id)
    return investigation


async def test_rank_scope_puts_the_most_relevant_paper_first(db_session):
    investigation = await saved_scope(
        db_session,
        record("W1", "Protein folding with graph neural networks"),               # source rank 1
        record("W2", "Weather forecasting with recurrent networks"),             # source rank 2
        record("W3", "Measuring hallucination in small language models"),        # source rank 3
    )
    embedder = FakeEmbedder()
    ranked = await rank_scope(db_session, embedder, investigation_id=investigation.id,
                              query="hallucination of small language models")

    assert [item.paper.openalex_id for item in ranked][0] == "W3"  # our ranking overrides the source order
    assert ranked[0].hit.source_rank == 3
    assert all(item.hit.embed_score == pytest.approx(item.similarity) for item in ranked)
    assert ranked[0].similarity > ranked[1].similarity


async def test_ranking_again_does_not_re_embed_papers(db_session):
    investigation = await saved_scope(db_session, record("W1", "Small language models"),
                                      record("W2", "Graph neural networks"))
    embedder = FakeEmbedder()
    await rank_scope(db_session, embedder, investigation_id=investigation.id, query="language models")
    await rank_scope(db_session, embedder, investigation_id=investigation.id, query="graph networks")
    documents_embedded = sum(count for task, count in embedder.calls if task == "RETRIEVAL_DOCUMENT")
    assert documents_embedded == 2  # only the first ranking embedded the papers


async def test_ranking_stays_inside_its_scope(db_session):
    evaluation = uuid.uuid4()
    investigation = await saved_scope(db_session, record("W1", "Small language models"), evaluation_id=evaluation)
    landscape = await rank_scope(db_session, FakeEmbedder(), investigation_id=investigation.id, query="models")
    evaluation_scope = await rank_scope(db_session, FakeEmbedder(), investigation_id=investigation.id,
                                        query="models", evaluation_id=evaluation)
    assert landscape == []  # nothing was saved in the landscape scope
    assert [item.paper.openalex_id for item in evaluation_scope] == ["W1"]


async def test_empty_scope(db_session):
    assert await rank_scope(db_session, FakeEmbedder(), investigation_id=uuid.uuid4(), query="anything") == []
