"""The user's own work is never prior art against them (doc 02 §2.4, step 6a.2). Pure code, no Claude.

set_aside_own_work(session, evaluation_id) runs before papers are screened (screening and every Skeptic round,
through score_unscreened). When the investigation has a user document, a search hit is the user's own work if:
  - its DOI or arXiv ID equals the one the user gave (arXiv versions ignored), or
  - the user said the document is published and the paper's title matches the document's title (the first
    heading of the document, or the idea's title) closely enough (OWN_TITLE_MATCH).
Such a hit is marked screened with score 0 and the reason OWN_WORK_REASON, so it is never read, never part of
the prior-work matrix and never in a verdict. Its paper ID is recorded in the evaluation's counters
("own_work"), and the report lists it.
"""

from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher

from sqlalchemy import select

from src.db.models import Candidate, Evaluation, Paper, RetrievalHit, UserDocument

OWN_WORK_REASON = "your own work: never counted as prior work against you"
OWN_TITLE_MATCH = 0.9
_WORDS = re.compile(r"[a-z0-9]+")


def _norm_title(title: str) -> str:
    return " ".join(_WORDS.findall((title or "").lower()))


def _norm_arxiv(value: str | None) -> str:
    value = (value or "").lower().removeprefix("arxiv:").strip()
    return re.sub(r"v\d+$", "", value)


def title_matches(paper_title: str, titles: list[str]) -> bool:
    paper = _norm_title(paper_title)
    return bool(paper) and any(
        SequenceMatcher(None, paper, _norm_title(t)).ratio() >= OWN_TITLE_MATCH for t in titles if _norm_title(t))


def is_own_work(paper: Paper, document: UserDocument, titles: list[str]) -> bool:
    ids = document.external_ids or {}
    if ids.get("doi") and paper.doi and paper.doi.lower() == ids["doi"].lower().removeprefix("https://doi.org/"):
        return True
    if ids.get("arxiv") and paper.arxiv_id and _norm_arxiv(paper.arxiv_id) == _norm_arxiv(ids["arxiv"]):
        return True
    return bool(document.published) and title_matches(paper.title, titles)


async def set_aside_own_work(session, evaluation_id: uuid.UUID) -> list[uuid.UUID]:
    """Marks the evaluation's unscreened hits that are the user's own work. Returns their paper IDs."""
    evaluation = await session.get(Evaluation, evaluation_id)
    candidate = await session.get(Candidate, evaluation.candidate_id)
    document = (await session.execute(select(UserDocument).where(
        UserDocument.investigation_id == candidate.investigation_id))).scalar_one_or_none()
    if document is None:
        return []
    user_idea = (await session.execute(select(Candidate).where(
        Candidate.investigation_id == candidate.investigation_id, Candidate.origin == "user",
        Candidate.version == 1))).scalar_one_or_none()
    titles = [s["heading"] for s in (document.sections or [])[:1] if s.get("heading") != "(start)"]
    if user_idea is not None:
        titles.append(user_idea.title)
    found = []
    rows = (await session.execute(
        select(RetrievalHit, Paper).join(Paper, Paper.id == RetrievalHit.paper_id)
        .where(RetrievalHit.evaluation_id == evaluation_id, RetrievalHit.screened.is_(False)))).all()
    for hit, paper in rows:
        if is_own_work(paper, document, titles):
            hit.screened, hit.llm_score, hit.llm_reason = True, 0, OWN_WORK_REASON
            found.append(paper.id)
    if found:
        known = list((evaluation.counters or {}).get("own_work", []))
        evaluation.counters = {**(evaluation.counters or {}),
                               "own_work": known + [str(p) for p in found if str(p) not in known]}
    await session.flush()
    return found
