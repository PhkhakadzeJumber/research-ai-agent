"""Tests for the Paper Reader agent (src/ai/agents/paper_reader.py) with fake models: no network."""

import asyncio
import json
import uuid

import pytest
from google.genai import types

from src.ai.agents.paper_reader import extractor_version, paper_for_reading, read_paper, read_papers
from src.ai.context import ChunkForReading, PaperForReading
from src.config import RoleLlmSettings
from src.db.models import Chunk, Paper
from src.llm import LlmRefusal
from tests.fakes import PaperAwareLlm, ScriptedLlm, answer

SETTINGS = RoleLlmSettings(role="paper_reader", model="claude-opus-5-5", effort="low", max_tokens=16000)
ABSTRACT = ("We study retrieval-augmented generation for low-resource languages. Retrieval reduces "
            "hallucinations of small language models on the AfriQA benchmark.")


def analysis(*limitations, finding_quote=None, chunk_id=None, datasets=()):
    output = {"contribution_type": "empirical_study", "datasets": list(datasets),
              "limitations": [{"statement": "s", "quote": {"text": text, "chunk_id": chunk}}
                              for text, chunk in limitations]}
    if finding_quote:
        output["findings"] = [{"setting": "RAG vs closed-book", "claim": "RAG helps", "direction": "positive",
                               "quote": {"text": finding_quote, "chunk_id": chunk_id}}]
    return json.dumps(output)


def read(paper, llm):
    return asyncio.run(read_paper(paper, settings=SETTINGS, model=llm))


# --- one paper ----------------------------------------------------------------------------------------

def test_reading_an_abstract_verifies_its_quotes():
    paper = PaperForReading(paper_id="p_1", title="RAG for LRLs", abstract=ABSTRACT)
    llm = ScriptedLlm.with_answers(answer(analysis(
        finding_quote="Retrieval reduces hallucinations of small language models",
        datasets=["AfriQA", "TyDi QA"],
    )))
    reading = read(paper, llm)

    assert (reading.paper_id, reading.source_level, reading.model) == ("p_1", "abstract", "claude-opus-5-5")
    assert reading.extractor_version == extractor_version() == "reader-p1.1-s1"
    assert reading.verification.verification_rate == 1.0
    assert [check.name for check in reading.verification.missing_names] == ["TyDi QA"]  # not in the text
    assert reading.cost_usd is not None and len(reading.records) == 1
    assert '<paper id="p_1">' in llm.requests[0].contents[-1].parts[0].text


def test_invented_quotes_are_flagged_not_trusted():
    paper = PaperForReading(paper_id="p_1", title="T", abstract=ABSTRACT)
    reading = read(paper, ScriptedLlm.with_answers(answer(analysis(
        ("We only evaluate models above seven billion parameters", None),  # not in the abstract
    ))))
    [check] = reading.verification.quotes
    assert not check.verified and "not found" in check.reason


def test_full_text_quotes_are_checked_in_their_chunk():
    paper = PaperForReading(paper_id="p_1", title="T", abstract=ABSTRACT, chunks=(
        ChunkForReading("c_1", "Limitations", "We only evaluate models with at least seven billion parameters."),
    ))
    reading = read(paper, ScriptedLlm.with_answers(answer(analysis(
        ("We only evaluate models with at least seven billion parameters", "c_1"),
    ))))
    assert reading.source_level == "full_text"
    assert reading.verification.verification_rate == 1.0


def test_a_paper_without_text_is_refused_before_calling_claude():
    llm = ScriptedLlm.with_answers()
    with pytest.raises(ValueError, match="neither an abstract nor full-text chunks"):
        read(PaperForReading(paper_id="p_1", title="Only a title"), llm)
    assert llm.requests == []  # no tokens spent


def test_stored_rows_become_what_the_reader_sees():
    paper = Paper(id=uuid.uuid4(), title="T", abstract=ABSTRACT, tier=1, arxiv_id="2401.00001",
                  year=2024, venue="ACL", authors=["Ada Lovelace"])
    chunks = [Chunk(id=uuid.uuid4(), paper_id=paper.id, chunker_version="v1", position=1, text="second"),
              Chunk(id=uuid.uuid4(), paper_id=paper.id, chunker_version="v1", position=0, section_title="Intro",
                    text="first")]
    view = paper_for_reading(paper, chunks)

    assert view.paper_id == str(paper.id)
    assert (view.year, view.venue, view.authors) == (2024, "ACL", ("Ada Lovelace",))
    assert [chunk.text for chunk in view.chunks] == ["first", "second"]  # in reading order
    assert view.chunks[0].chunk_id == str(chunks[1].id)


# --- many papers --------------------------------------------------------------------------------------

def papers(count):
    return [PaperForReading(paper_id=f"p_{i}", title=f"Paper {i}", abstract=ABSTRACT) for i in range(count)]


def test_papers_are_read_in_parallel_up_to_the_limit():
    llm = PaperAwareLlm.with_answers({f"p_{i}": answer(analysis()) for i in range(6)}, delay=0.2)
    outcomes = asyncio.run(read_papers(papers(6), concurrency=3, settings=SETTINGS, model=llm))

    assert [outcome.paper_id for outcome in outcomes] == [f"p_{i}" for i in range(6)]  # input order kept
    assert all(outcome.reading is not None for outcome in outcomes)
    assert llm.max_running == 3  # parallel, but never more than the limit


def test_one_failing_paper_does_not_stop_the_others():
    llm = PaperAwareLlm.with_answers({
        "p_0": answer(analysis()),
        "p_1": answer("", types.FinishReason.SAFETY),  # refusal
        "p_2": answer("not json"),  # same invalid answer to the repair attempt -> LlmOutputInvalid
    })
    outcomes = asyncio.run(read_papers(papers(3), settings=SETTINGS, model=llm))

    assert outcomes[0].reading is not None and outcomes[0].error is None
    assert outcomes[1].reading is None and outcomes[1].error.startswith(LlmRefusal.__name__)
    assert outcomes[2].reading is None and "LlmOutputInvalid" in outcomes[2].error
