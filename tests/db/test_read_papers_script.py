"""Tests for scripts/read_papers.py: choosing papers (real test database, rolled back) and output."""

import itertools
import uuid
from decimal import Decimal

import pytest

from scripts.read_papers import (
    ChosenPaper,
    InputError,
    choose_papers,
    format_paper,
    format_summary,
    main,
    parse_args,
    resolve_investigation,
)
from src.ai.workflows.reading import LibraryOutcome
from src.db.models import Paper, PaperAnalysis, RetrievalHit
from src.db.repositories.runs import create_investigation

pytestmark = pytest.mark.db

_paper_numbers = itertools.count()  # unique arXiv IDs across all searches in a test


async def saved_search(session, *, scores):
    """An investigation with one landscape hit per (source_rank, embed_score) pair."""
    investigation = await create_investigation(session, mode="discover", complexity="beginner",
                                               input={"query": "rag"})
    for index, (source_rank, embed_score) in enumerate(scores):
        paper = Paper(title=f"Paper {index}", abstract="Some text.", tier=2,
                      arxiv_id=f"2401.{next(_paper_numbers):05d}")
        session.add(paper)
        await session.flush()
        session.add(RetrievalHit(investigation_id=investigation.id, paper_id=paper.id, source_rank=source_rank,
                                 embed_score=embed_score))
    await session.flush()
    return investigation


def titles(chosen):
    return [item.paper.title for item in chosen]


@pytest.mark.anyio
async def test_ranked_search_uses_eurekas_ranking_by_default(db_session):
    investigation = await saved_search(db_session, scores=[(1, 0.2), (2, 0.9), (3, 0.5)])
    chosen, order = await choose_papers(db_session, investigation.id, top=2)
    assert order == "ranking"
    assert titles(chosen) == ["Paper 1", "Paper 2"]  # highest similarity first


@pytest.mark.anyio
async def test_source_order_can_be_forced(db_session):
    investigation = await saved_search(db_session, scores=[(2, 0.9), (1, 0.2)])
    chosen, order = await choose_papers(db_session, investigation.id, top=5, order="source")
    assert order == "source" and titles(chosen) == ["Paper 1", "Paper 0"]


@pytest.mark.anyio
async def test_unranked_search_falls_back_to_source_order(db_session):
    investigation = await saved_search(db_session, scores=[(2, None), (1, None)])
    chosen, order = await choose_papers(db_session, investigation.id, top=5)
    assert order == "source" and titles(chosen) == ["Paper 1", "Paper 0"]
    with pytest.raises(InputError, match="wasn't ranked"):
        await choose_papers(db_session, investigation.id, top=5, order="ranking")


@pytest.mark.anyio
async def test_latest_and_unknown_investigations(db_session):
    older = await saved_search(db_session, scores=[(1, None)])
    newer = await saved_search(db_session, scores=[(1, None)])
    assert (await resolve_investigation(db_session, None)).id in {older.id, newer.id}
    with pytest.raises(InputError, match="No investigation"):
        await resolve_investigation(db_session, uuid.uuid4())


# --- output (no database needed) ---------------------------------------------------------------------------

def make_chosen(title="Retrieval for low-resource QA"):
    return ChosenPaper(paper=Paper(title=title, year=2025, tier=2, arxiv_id="2401.00001"), hit=RetrievalHit())


def test_format_paper_with_analysis_and_details():
    analysis = PaperAnalysis(source_level="abstract", content={
        "contribution_type": "empirical_study", "methods": ["RAG"], "datasets": ["AfriQA"], "tasks": [],
        "research_questions": [{"statement": "Does RAG help?", "quote": {"verified": True}}],
        "findings": [{"claim": "RAG helps", "direction": "positive", "quote": {"verified": False}}],
        "limitations": [], "future_work": [],
        "verification": {"quotes": 2, "verified_quotes": 1, "missing_names": ["TyDi QA"]},
    })
    outcome = LibraryOutcome(paper_id=uuid.uuid4(), analysis_id=uuid.uuid4(), verified_quotes=1, quotes=2)
    text = format_paper(1, make_chosen(), outcome, analysis, details=True)

    assert " 1. Retrieval for low-resource QA (2025)" in text
    assert "read now | abstract | contribution: empirical_study | quotes verified: 1/2" in text
    assert "methods: RAG" in text and "datasets: AfriQA" in text
    assert "names not found in the text: TyDi QA" in text
    assert "RQ ✓ Does RAG help?" in text and "finding ✗ [positive] RAG helps" in text


def test_format_failed_paper():
    outcome = LibraryOutcome(paper_id=uuid.uuid4(), error="LlmRefusal: declined")
    assert "FAILED: LlmRefusal: declined" in format_paper(2, make_chosen(), outcome, None)


def test_summary():
    outcomes = [
        LibraryOutcome(uuid.uuid4(), uuid.uuid4(), verified_quotes=3, quotes=3),
        LibraryOutcome(uuid.uuid4(), uuid.uuid4(), reused=True, verified_quotes=1, quotes=2),
        LibraryOutcome(uuid.uuid4(), error="ValueError: no text"),
    ]
    assert format_summary(outcomes, Decimal("0.0412")) == (
        "Papers: 1 read, 1 reused, 1 failed | quotes verified: 4/5 (80%) | cost of this run: $0.0412"
    )


def test_arguments():
    args = parse_args(["--latest", "--top", "3", "--order", "source", "--details"])
    assert (args.latest, args.top, args.order, args.details) == (True, 3, "source", True)
    with pytest.raises(SystemExit):
        parse_args([])  # --investigation or --latest is required


def test_invalid_top_exits_with_code_2(capsys):
    assert main(["--latest", "--top", "0"]) == 2
    assert "--top and --concurrency must be at least 1" in capsys.readouterr().err
