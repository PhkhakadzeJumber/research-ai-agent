"""Tests for the paper library routes (step 9a): the list with its filters and search, and one paper.
Real test database (rolled back), data from the real pipeline with fake Claude: no network, no cost."""

import uuid

import pytest

from src.db.models import Paper, PaperAnalysis, Quote
from tests.api.test_api_investigations import with_package

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def test_the_library_list_and_its_filters(client, db_session):
    investigation_id, _ = await with_package(db_session)
    db_session.add(Paper(title="An unread paper about 100% recall_rates", abstract="Nothing here.", year=2019,
                         openalex_id="W1", tier=2))
    await db_session.flush()

    page = (await client.get("/api/v1/papers?limit=100")).json()
    assert page["total"] == len(page["items"]) > 1
    first = page["items"][0]
    assert first["read"] is True and first["source_level"] == "abstract"  # read papers come first
    assert first["verified_quotes"] >= 1 and first["investigations"] == 1
    assert page["items"][-1]["title"].startswith("An unread paper") and page["items"][-1]["read"] is False

    unread = (await client.get("/api/v1/papers?read=false")).json()
    assert [p["title"] for p in unread["items"]] == ["An unread paper about 100% recall_rates"]
    assert (await client.get("/api/v1/papers?used=false")).json()["total"] == 1
    assert (await client.get("/api/v1/papers?read=true")).json()["total"] == page["total"] - 1
    assert (await client.get("/api/v1/papers?year_from=2018")).json()["total"] == 1

    # search: case-insensitive, and % and _ are matched literally, not as wildcards
    assert (await client.get("/api/v1/papers", params={"q": "UNREAD"})).json()["total"] == 1
    assert (await client.get("/api/v1/papers", params={"q": "100% recall_rates"})).json()["total"] == 1
    assert (await client.get("/api/v1/papers", params={"q": "1%0"})).json()["total"] == 0
    assert (await client.get("/api/v1/papers", params={"q": "paper 0"})).json()["total"] >= 1  # abstract or title


async def test_one_paper_with_eurekas_reading(client, db_session):
    investigation_id, _ = await with_package(db_session)
    page = (await client.get("/api/v1/papers?read=true&limit=1")).json()
    paper = (await client.get(f"/api/v1/papers/{page['items'][0]['id']}")).json()

    assert paper["analysis"]["source_level"] == "abstract" and paper["abstract"]
    assert paper["links"]["openalex"].startswith("https://openalex.org/")
    assert paper["link"] == paper["links"]["openalex"]
    assert paper["quotes"] and paper["quotes"][0]["verified"] is True
    [use] = paper["used_in"]
    assert use["investigation_id"] == str(investigation_id) and use["mode"] == "evaluate"
    assert use["roles"][:2] == ["found", "read"] and "evidence for a claim" in use["roles"]

    missing = await client.get(f"/api/v1/papers/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "Paper not found."


async def test_the_findings_come_with_their_quotes(client, db_session):
    # found in step 10d: a stored analysis keeps each point's quote as a reference (quote_id), and the API read a
    # text field that isn't there, so every quote came out empty
    paper = Paper(title="Tokenizers and low-resource languages", abstract="French needs 31% more tokens.", year=2026,
                  openalex_id="W2", tier=2)
    db_session.add(paper)
    await db_session.flush()
    good = Quote(paper_id=paper.id, text="French needs 31% more tokens", text_hash="a", source_level="abstract",
                 verified=True)
    bad = Quote(paper_id=paper.id, text="a sentence the abstract doesn't contain", text_hash="b",
                source_level="abstract", verified=False)
    db_session.add_all([good, bad])
    await db_session.flush()
    db_session.add(PaperAnalysis(paper_id=paper.id, extractor_version="v", model="m", source_level="abstract", content={
        "findings": [{"claim": "French costs more tokens.", "setting": "seven tokenizers", "direction": "positive",
                      "quote": {"quote_id": str(good.id), "verified": True, "reason": None}}],
        "limitations": [{"statement": "Only seven tokenizers.", "quote": {"quote_id": str(bad.id), "verified": False}}],
        "future_work": [{"statement": "No quote here.", "quote": None}],
    }))
    await db_session.flush()

    analysis = (await client.get(f"/api/v1/papers/{paper.id}")).json()["analysis"]
    [finding] = analysis["findings"]
    assert (finding["text"], finding["quote"], finding["verified"]) == (
        "French costs more tokens.", "French needs 31% more tokens", True)
    [limitation] = analysis["limitations"]
    assert (limitation["quote"], limitation["verified"]) == ("a sentence the abstract doesn't contain", False)
    assert analysis["future_work"] == [{"text": "No quote here.", "quote": "", "verified": False}]
