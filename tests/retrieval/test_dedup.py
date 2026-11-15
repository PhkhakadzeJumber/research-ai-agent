"""Tests for src/retrieval/dedup.py: what must merge, what must never merge, and how merges combine."""

import json
from pathlib import Path

from src.retrieval.dedup import (
    REASON_ARXIV,
    REASON_DOI,
    REASON_TITLE,
    deduplicate,
    merge_records,
    normalize_title,
    surname,
)
from src.retrieval.records import ExternalIds, PaperRecord
from src.retrieval.sources.arxiv import parse_feed
from src.retrieval.sources.openalex import parse_work

FIXTURES = Path(__file__).parent.parent / "fixtures"
TITLE = "Retrieval Augmented Generation for Low Resource Languages"


def paper(*, doi=None, arxiv=None, openalex=None, title=TITLE, year=2024, authors=("Ada Lovelace",),
          source="openalex", tier=1, **extra):
    return PaperRecord(
        ids=ExternalIds(doi=doi, arxiv_id=arxiv, openalex_id=openalex),
        title=title, sources=(source,), tier=tier, year=year, authors=authors, **extra,
    )


# --- helpers ------------------------------------------------------------------------

def test_normalize_title_ignores_case_accents_and_punctuation():
    assert normalize_title("Ragas:  Automated Évaluation!") == "ragas automated evaluation"


def test_surname_handles_both_name_orders():
    assert surname("Shahul Es") == "es"
    assert surname("Es, Shahul") == "es"
    assert surname("José García") == "garcia"


# --- must merge ------------------------------------------------------------------------

def test_same_doi_merges_even_when_spelled_differently():
    result = deduplicate([
        paper(doi="10.1/ABC", openalex="W1"),
        paper(doi="https://doi.org/10.1/abc", title="Other Title Entirely Different Words", source="semantic_scholar"),
    ])
    assert len(result.papers) == 1
    assert result.merges[0].reasons == (REASON_DOI,)


def test_preprint_and_published_version_merge_through_arxiv_id():
    preprint = paper(arxiv="2405.01234", source="arxiv", tier=2, year=2023)
    published = paper(doi="10.1/pub", arxiv="2405.01234", openalex="W1", year=2024)  # OpenAlex knows both IDs
    result = deduplicate([preprint, published])

    assert len(result.papers) == 1
    merged = result.papers[0]
    assert merged.tier == 1
    assert merged.year == 2023  # earliest public appearance
    assert merged.sources == ("arxiv", "openalex")
    assert REASON_ARXIV in result.merges[0].reasons


def test_arxiv_record_with_published_doi_merges_with_published_record():
    from_arxiv = paper(arxiv="2508.03553", doi="10.1109/icde.2025.1", source="arxiv", tier=2)
    from_openalex = paper(doi="10.1109/icde.2025.1", openalex="W9", source="openalex", tier=1)
    assert len(deduplicate([from_arxiv, from_openalex]).papers) == 1


def test_chains_of_shared_ids_merge_into_one():
    a = paper(arxiv="2405.01234", source="arxiv", tier=2)
    b = paper(arxiv="2405.01234", doi="10.1/x", source="openalex")
    c = paper(doi="10.1/x", openalex="W5", source="semantic_scholar")
    result = deduplicate([a, b, c])
    assert len(result.papers) == 1
    assert result.merges[0].merged_count == 3


def test_titles_differing_only_in_punctuation_accents_and_case_merge():
    a = paper(openalex="W1", title="Retrieval-Augmented Generation for Low-Resource Languages")
    b = paper(arxiv="2401.00001", title="retrieval augmented generation for low resource languages.",
              source="arxiv", tier=2)
    result = deduplicate([a, b])
    assert len(result.papers) == 1
    assert result.merges[0].reasons == (REASON_TITLE,)


def test_nearly_identical_titles_merge():
    a = paper(openalex="W1", title="Retrieval Augmented Generation for Low Resource Languages")
    b = paper(openalex="W2", title="Retrieval Augmented Generation for Low Resource Language")
    assert len(deduplicate([a, b]).papers) == 1


def test_missing_years_or_authors_do_not_block_a_title_match():
    a = paper(openalex="W1", year=None, authors=())
    b = paper(openalex="W2")
    assert len(deduplicate([a, b]).papers) == 1


# --- must NOT merge ----------------------------------------------------------------------

def test_same_title_years_far_apart_stay_separate():
    a = paper(openalex="W1", year=2015)
    b = paper(openalex="W2", year=2023)
    assert len(deduplicate([a, b]).papers) == 2


def test_same_title_different_first_author_stay_separate():
    a = paper(openalex="W1", authors=("Ada Lovelace",))
    b = paper(openalex="W2", authors=("Alan Turing",))
    assert len(deduplicate([a, b]).papers) == 2


def test_different_published_dois_stay_separate():
    conference = paper(doi="10.1/conf", openalex="W1")
    journal_extension = paper(doi="10.1/journal", openalex="W2")
    assert len(deduplicate([conference, journal_extension]).papers) == 2


def test_different_arxiv_ids_stay_separate():
    a = paper(arxiv="2401.00001", source="arxiv", tier=2)
    b = paper(arxiv="2402.00002", source="arxiv", tier=2)
    assert len(deduplicate([a, b]).papers) == 2


def test_arxiv_doi_does_not_block_a_title_match():
    # OpenAlex gives arXiv preprints a 10.48550/arxiv.* DOI; that's not a second publication.
    a = paper(doi="10.48550/arxiv.2401.00001", openalex="W1", tier=2)
    b = paper(doi="10.1/published", openalex="W2")
    assert len(deduplicate([a, b]).papers) == 1


def test_short_generic_titles_never_merge_by_title():
    a = paper(openalex="W1", title="Introduction")
    b = paper(openalex="W2", title="Introduction")
    assert len(deduplicate([a, b]).papers) == 2


def test_different_titles_stay_separate():
    a = paper(openalex="W1", title="Retrieval Augmented Generation for Low Resource Languages")
    b = paper(openalex="W2", title="Retrieval Augmented Generation for Medical Question Answering")
    assert len(deduplicate([a, b]).papers) == 2


def test_title_match_cannot_join_groups_with_conflicting_ids():
    # a and b are one work (same DOI). c has the same title but another published DOI.
    a = paper(doi="10.1/a", openalex="W1")
    b = paper(doi="10.1/a", arxiv="2401.00001", source="arxiv", tier=2)
    c = paper(doi="10.1/c", openalex="W3")
    assert len(deduplicate([a, b, c]).papers) == 2


# --- how merged records are built -----------------------------------------------------------

def test_merge_rules_for_each_field():
    preprint = paper(
        arxiv="2405.01234", source="arxiv", tier=2, year=2023, title="A Preprint Title Of Four",
        abstract="A long and complete abstract from arXiv.", authors=("A One", "B Two", "C Three"),
        venue="arXiv", open_access_pdf_url="https://arxiv.org/pdf/2405.01234",
    )
    published = paper(
        doi="10.1/pub", arxiv="2405.01234", openalex="W1", tier=1, year=2024, title="The Published Title",
        abstract="Short.", authors=("A One",), venue="ACL", citation_count=57,
        open_access_pdf_url="https://publisher.example.org/paper.pdf",
    )
    merged = merge_records([preprint, published])

    assert merged.title == "The Published Title"  # from the published version
    assert merged.venue == "ACL"
    assert merged.tier == 1
    assert merged.year == 2023  # earliest
    assert merged.abstract == "A long and complete abstract from arXiv."  # longest
    assert merged.authors == ("A One", "B Two", "C Three")  # most complete
    assert merged.citation_count == 57
    assert merged.open_access_pdf_url == "https://arxiv.org/pdf/2405.01234"  # arXiv preferred
    assert (merged.ids.doi, merged.ids.arxiv_id, merged.ids.openalex_id) == ("10.1/pub", "2405.01234", "W1")
    assert merged.sources == ("arxiv", "openalex")


def test_published_doi_wins_over_arxiv_doi():
    a = paper(doi="10.48550/arxiv.2405.01234", arxiv="2405.01234", openalex="W1", tier=2)
    b = paper(doi="10.1/pub", arxiv="2405.01234", source="semantic_scholar", tier=1)
    assert deduplicate([a, b]).papers[0].ids.doi == "10.1/pub"


# --- stability -----------------------------------------------------------------------------------

def test_output_follows_first_appearance_and_counts_are_reported():
    a = paper(openalex="W1", title="First Distinct Paper About Something")
    b = paper(openalex="W2", title="Second Distinct Paper About Something Else")
    a_again = paper(openalex="W1", title="First Distinct Paper About Something", source="semantic_scholar")
    result = deduplicate([a, b, a_again])

    assert [p.ids.openalex_id for p in result.papers] == ["W1", "W2"]
    assert (result.input_count, result.removed_count) == (3, 1)


def test_deduplicating_twice_changes_nothing():
    records = [
        paper(arxiv="2405.01234", source="arxiv", tier=2),
        paper(doi="10.1/pub", arxiv="2405.01234", openalex="W1"),
        paper(openalex="W2", title="Another Distinct Paper About Retrieval Methods"),
    ]
    once = deduplicate(records).papers
    assert deduplicate(once).papers == once


def test_empty_input():
    result = deduplicate([])
    assert result.papers == () and result.merges == () and result.input_count == 0


# --- real recorded data -------------------------------------------------------------------------

def test_real_openalex_and_arxiv_results_have_no_false_merges():
    openalex_works = json.loads((FIXTURES / "openalex" / "search_works.json").read_text(encoding="utf-8"))["results"]
    openalex_papers = [p for p in (parse_work(w) for w in openalex_works) if p]
    arxiv_papers = parse_feed((FIXTURES / "arxiv" / "search.xml").read_text(encoding="utf-8"))

    result = deduplicate(openalex_papers + arxiv_papers)
    # The two recorded searches share no papers, so nothing may be merged.
    assert len(result.papers) == len(openalex_papers) + len(arxiv_papers)
    assert result.merges == ()
