"""Tests for the science library tables: the database must enforce every integrity rule."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from src.db.models import Chunk, Paper, PaperAnalysis, Quote
from src.db.models.library import quote_text_hash

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def make_paper(**overrides):
    values = dict(title="Retrieval Augmented Generation for Low Resource Languages", tier=1, arxiv_id="2405.01234")
    values.update(overrides)
    return Paper(**values)


async def add_and_flush(session, *objects):
    session.add_all(objects)
    await session.flush()


async def assert_rejected(session, *objects, constraint):
    """The database must refuse these rows, naming the violated constraint."""
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():  # a savepoint, so the session stays usable afterwards
            await add_and_flush(session, *objects)


# --- papers ---------------------------------------------------------------------------------

async def test_paper_round_trip_with_defaults(db_session):
    paper = make_paper(authors=["Ada Lovelace", "Alan Turing"], year=2024, citation_count=12)
    await add_and_flush(db_session, paper)
    await db_session.refresh(paper)

    assert paper.authors == ["Ada Lovelace", "Alan Turing"]
    assert paper.full_text_status == "none"
    assert paper.created_at.tzinfo is not None


async def test_paper_needs_an_external_id(db_session):
    await assert_rejected(db_session, make_paper(arxiv_id=None), constraint="ck_papers_has_external_id")


async def test_external_ids_are_unique(db_session):
    await add_and_flush(db_session, make_paper(doi="10.1/abc", arxiv_id=None))
    await assert_rejected(db_session, make_paper(doi="10.1/abc", arxiv_id=None), constraint="uq_papers_doi")


async def test_papers_without_a_doi_do_not_collide(db_session):
    # Many papers have no DOI: empty values must not count as duplicates.
    await add_and_flush(db_session, make_paper(arxiv_id="2401.00001"), make_paper(arxiv_id="2401.00002"))


@pytest.mark.parametrize(
    "overrides, constraint",
    [
        ({"tier": 3}, "ck_papers_tier_valid"),
        ({"year": 1500}, "ck_papers_year_plausible"),
        ({"citation_count": -1}, "ck_papers_citations_not_negative"),
        ({"full_text_status": "maybe"}, "ck_papers_full_text_status_valid"),
    ],
)
async def test_invalid_paper_values_are_rejected(db_session, overrides, constraint):
    await assert_rejected(db_session, make_paper(**overrides), constraint=constraint)


# --- chunks ---------------------------------------------------------------------------------

async def test_chunk_keyword_search_works(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    await add_and_flush(
        db_session,
        Chunk(paper_id=paper.id, chunker_version="v1", position=0, section_title="Limitations",
              text="We only evaluate models with at least seven billion parameters."),
        Chunk(paper_id=paper.id, chunker_version="v1", position=1, section_title="Results",
              text="Retrieval improves exact match on every benchmark."),
    )
    found = (await db_session.execute(
        select(Chunk.position).where(Chunk.search_vector.op("@@")(func.plainto_tsquery("english", "evaluated models")))
    )).scalars().all()
    assert found == [0]  # matches "evaluate ... models" (stemmed), not the other chunk


async def test_chunk_positions_are_unique_per_chunker_version(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    await add_and_flush(db_session, Chunk(paper_id=paper.id, chunker_version="v1", position=0, text="a"))
    await add_and_flush(db_session, Chunk(paper_id=paper.id, chunker_version="v2", position=0, text="a"))  # new set: fine
    await assert_rejected(
        db_session, Chunk(paper_id=paper.id, chunker_version="v1", position=0, text="b"),
        constraint="uq_chunks_paper_id_chunker_version_position",
    )


# --- paper analyses ---------------------------------------------------------------------------

async def test_one_analysis_per_paper_extractor_version_and_source_level(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)

    def analysis(level, version="reader-v1"):
        return PaperAnalysis(paper_id=paper.id, extractor_version=version, model="m", source_level=level,
                             content={"methods": ["RAG"]})

    await add_and_flush(db_session, analysis("abstract"), analysis("full_text"), analysis("abstract", "reader-v2"))
    await assert_rejected(db_session, analysis("abstract"),
                          constraint="uq_paper_analyses_paper_id_extractor_version_source_level")


async def test_analysis_content_is_stored_as_json(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    content = {"methods": ["RAG"], "limitations": [{"text": "only large models", "quote_id": "q1"}]}
    await add_and_flush(db_session, PaperAnalysis(paper_id=paper.id, extractor_version="v1", model="m",
                                                  source_level="abstract", content=content))
    stored = (await db_session.execute(
        select(PaperAnalysis.content["limitations"][0]["text"].astext)
    )).scalar_one()
    assert stored == "only large models"


# --- quotes -----------------------------------------------------------------------------------

def make_quote(paper, chunk=None, quote_text="we only evaluate large models"):
    return Quote(paper_id=paper.id, chunk_id=chunk.id if chunk else None, text=quote_text,
                 text_hash=quote_text_hash(quote_text), source_level="full_text" if chunk else "abstract")


async def test_quotes_default_to_unverified(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    quote = make_quote(paper)
    await add_and_flush(db_session, quote)
    await db_session.refresh(quote)
    assert quote.verified is False and quote.verification_score is None


async def test_duplicate_abstract_quotes_are_rejected(db_session):
    # chunk_id is NULL for abstract quotes; NULLS NOT DISTINCT makes them unique anyway.
    paper = make_paper()
    await add_and_flush(db_session, paper)
    await add_and_flush(db_session, make_quote(paper))
    await assert_rejected(db_session, make_quote(paper), constraint="uq_quotes_paper_id_chunk_id_text_hash")


async def test_same_quote_text_in_different_chunks_is_allowed(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    first = Chunk(paper_id=paper.id, chunker_version="v1", position=0, text="x")
    second = Chunk(paper_id=paper.id, chunker_version="v1", position=1, text="y")
    await add_and_flush(db_session, first, second)
    await add_and_flush(db_session, make_quote(paper, first), make_quote(paper, second))


async def test_source_level_must_match_the_chunk(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    wrong = make_quote(paper)
    wrong.source_level = "full_text"  # claims full text but points at no chunk
    await assert_rejected(db_session, wrong, constraint="ck_quotes_source_level_matches_chunk")


async def test_verification_score_must_be_between_zero_and_one(db_session):
    paper = make_paper()
    await add_and_flush(db_session, paper)
    quote = make_quote(paper)
    quote.verification_score = 1.5
    await assert_rejected(db_session, quote, constraint="ck_quotes_verification_score_range")


def test_quote_hash_ignores_whitespace_case_and_unicode_form():
    assert quote_text_hash("We  only\nevaluate") == quote_text_hash("we only evaluate")
    assert quote_text_hash("ﬁne-tuning") == quote_text_hash("fine-tuning")  # ligature vs plain letters
    assert quote_text_hash("we only evaluate") != quote_text_hash("we evaluate only")


async def test_tables_exist_after_migration(db_session):
    tables = (await db_session.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
    ))).scalars().all()
    assert {"papers", "chunks", "paper_analyses", "quotes", "alembic_version"} <= set(tables)
