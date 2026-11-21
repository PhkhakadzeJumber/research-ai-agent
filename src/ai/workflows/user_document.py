"""The user's own research document in Mode 1 (doc 02 §2.4; step 6a.1): from a file to the structured idea.

prepare_document(path) (code): checks the format (.md / .txt; PDF comes with step 6b) and the size
  (MAX_DOCUMENT_CHARS), and splits the text into sections D1, D2, ... by its headings (Markdown `#`
  headings; for plain text, by blank-line paragraphs), long sections in parts.
attach_document(session, investigation_id, prepared, consent_at=...): stores it privately with the
  investigation (UserDocument; never in the shared library). Consent is required: nothing is sent to Claude
  without it.
understand_document(sessions, investigation_id) replaces understand_idea() when a document is attached:
  1. the Paper Reader reads the sections (the same reader and quote verification as for papers); its
     reading is stored on the document, never in the shared library, and its quotes are "your document",
     never evidence for a verdict;
  2. the Idea Analyst's document task builds the structured idea from that reading: the usual reading is
     stored as the Intake (so clarification works unchanged), and the facet -> section links and the
     document's novelty claims are stored with the document (idea_extras);
  3. the investigation moves to `awaiting_clarification`, as with a typed idea.
If a call fails, the investigation fails (understanding is mandatory) and the calls made are recorded.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.ai.agents.idea_analyst import analyse_document
from src.ai.agents.paper_reader import read_paper
from src.ai.context import ChunkForReading, DocumentForAnalysis, IdeaForAnalysis, PaperForReading
from src.ai.schemas.structured_idea import SCHEMA_VERSION
from src.ai.workflows.states import Mode, Stage
from src.ai.workflows.transitions import advance_investigation, fail_investigation, start_investigation
from src.ai.workflows.understanding import Sessions, UnderstandingOutcome
from src.config import RoleLlmSettings
from src.db.models import Investigation, UserDocument
from src.db.repositories.intakes import store_intake
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmError

logger = logging.getLogger(__name__)

MAX_DOCUMENT_CHARS = 60_000  # about 15-20 pages; doc 02 §2.4
MEDIA_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
SECTION_CHARS = 3_000
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


class DocumentError(ValueError):
    """The file can't be used: unsupported format, empty, too long or not UTF-8 text."""


@dataclass(frozen=True)
class PreparedDocument:
    file_name: str
    media_type: str
    size_bytes: int
    sha256: str
    text: str
    sections: list[dict[str, str]]
    """[{"label": "D1", "heading": "...", "text": "..."}, ...]"""


def _parts(heading: str, body: str) -> list[tuple[str, str]]:
    body = body.strip()
    if not body:
        return []
    if len(body) <= SECTION_CHARS:
        return [(heading, body)]
    parts, current = [], ""
    for paragraph in re.split(r"\n\s*\n", body):
        if current and len(current) + len(paragraph) + 2 > SECTION_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}".strip()
        while len(current) > SECTION_CHARS:  # one huge paragraph
            parts.append(current[:SECTION_CHARS])
            current = current[SECTION_CHARS:]
    if current:
        parts.append(current)
    return [(heading if n == 1 else f"{heading} (part {n})", part) for n, part in enumerate(parts, start=1)]


def split_sections(text: str, media_type: str) -> list[dict[str, str]]:
    """Sections D1, D2, ... in reading order. Markdown splits at headings; plain text at blank-line paragraphs
    (the first line of each paragraph group serves as its heading)."""
    raw: list[tuple[str, str]] = []
    if media_type == "text/markdown":
        heading, body = "(start)", []
        for line in text.splitlines():
            match = _HEADING.match(line)
            if match:
                raw.extend(_parts(heading, "\n".join(body)))
                heading, body = match.group(2).strip(), []
            else:
                body.append(line)
        raw.extend(_parts(heading, "\n".join(body)))
    else:
        for paragraph in re.split(r"\n\s*\n", text):
            paragraph = paragraph.strip()
            if paragraph:
                first = paragraph.splitlines()[0]
                raw.extend(_parts(first[:80], paragraph))
    return [{"label": f"D{n}", "heading": heading, "text": body} for n, (heading, body) in enumerate(raw, start=1)]


def prepare_document(path: Path) -> PreparedDocument:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise DocumentError(f"{path.name}: only .md and .txt files are supported for now (PDF comes later).")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentError(f"{path.name} isn't UTF-8 text.") from None
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise DocumentError(f"{path.name} is empty.")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise DocumentError(f"{path.name} has {len(text):,} characters; the limit is {MAX_DOCUMENT_CHARS:,} "
                            "(about 15-20 pages). Shorten it, e.g. to the abstract, introduction and method.")
    return PreparedDocument(file_name=path.name, media_type=media_type, size_bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(), text=text,
                            sections=split_sections(text, media_type))


async def attach_document(session, investigation_id: uuid.UUID, prepared: PreparedDocument, *,
                          consent_at: datetime | None, published: bool | None = None,
                          external_ids: dict[str, str] | None = None) -> UserDocument:
    if consent_at is None:
        raise DocumentError("The user hasn't agreed to send the document to Claude: nothing can be read.")
    investigation = await session.get(Investigation, investigation_id)
    # recorded on the request too, so every later step knows the idea is a document (e.g. clarification)
    investigation.input = {**(investigation.input or {}), "document": prepared.file_name}
    document = UserDocument(investigation_id=investigation_id, file_name=prepared.file_name,
                            media_type=prepared.media_type, size_bytes=prepared.size_bytes, sha256=prepared.sha256,
                            text=prepared.text, sections=prepared.sections, consent_at=consent_at,
                            published=published, external_ids=external_ids or {})
    session.add(document)
    await session.flush()
    return document


@dataclass(frozen=True)
class DocumentOutcome(UnderstandingOutcome):
    facet_sources: dict[str, str] | None = None
    """facet text -> section label."""
    novelty_claims: list[dict[str, str]] | None = None
    reader_quotes: tuple[int, int] = (0, 0)
    """(verified, total) quotes in the Paper Reader's reading of the document."""


def _reading_items(analysis: dict[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The reader's analysis as short items, each prefixed with the section its verified quote is from."""
    def located(item: dict, text: str) -> str:
        quote = item.get("quote") or {}
        return f"({quote['chunk_id']}) {text}" if quote.get("chunk_id") else text

    out = []
    for name, field, key in (("research questions", "research_questions", "statement"),
                             ("findings", "findings", "claim"), ("limitations", "limitations", "statement"),
                             ("future work", "future_work", "statement"), ("assumptions", "assumptions", "statement")):
        out.append((name, tuple(located(item, item.get(key, "")) for item in analysis.get(field, []))))
    for name in ("tasks", "methods", "datasets", "metrics", "baselines"):
        out.append((name, tuple(analysis.get(name, []))))
    if analysis.get("setting"):
        out.append(("setting", (analysis["setting"],)))
    return tuple(out)


async def understand_document(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    read_settings: RoleLlmSettings | None = None,
    read_model: Any | None = None,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> DocumentOutcome:
    """Intake + understanding for a Mode 1 investigation (status `created`) with an attached document."""
    async with sessions() as session:
        investigation = await start_investigation(session, investigation_id)
        if investigation.mode != Mode.EVALUATE:
            raise DocumentError("Documents are for Mode 1 (evaluate my idea).")
        document = (await session.execute(select(UserDocument).where(
            UserDocument.investigation_id == investigation_id))).scalar_one()
        data = investigation.input or {}
        request = IdeaForAnalysis(
            idea=(data.get("idea") or "").strip(), complexity=investigation.complexity,
            field=data.get("field") or None, seed_papers=tuple(data.get("seed_papers") or ()),
            constraints=tuple((str(k).replace("_", " "), str(v))
                              for k, v in (investigation.constraints_snapshot or {}).items()
                              if v not in (None, "", [])))
        view = PaperForReading(paper_id=str(document.id), title=document.file_name,
                               chunks=tuple(ChunkForReading(s["label"], s["heading"], s["text"])
                                            for s in document.sections))
        await advance_investigation(session, investigation_id, to=Stage.UNDERSTANDING, expected_from=Stage.INTAKE)

    records = []
    try:
        reading = await read_paper(view, settings=read_settings, model=read_model)
        records += reading.records
        analysis = reading.analysis.model_dump(mode="json")
        checks = {c.location: c for c in reading.verification.quotes}
        for field in ("research_questions", "findings", "limitations", "future_work", "assumptions"):
            for index, item in enumerate(analysis.get(field, [])):
                check = checks.get(f"{field}[{index}]")
                item["quote"] = {**(item.get("quote") or {}), "verified": bool(check and check.verified)}
                if not item["quote"]["verified"]:
                    item["quote"]["chunk_id"] = None  # an unverified quote doesn't locate anything
        document_view = DocumentForAnalysis(
            title=document.file_name, sections=tuple((s["label"], s["heading"], s["text"]) for s in document.sections),
            reading=_reading_items(analysis))
        structured = await analyse_document(request, document_view, settings=settings, model=model)
        records += structured.records
    except LlmError as error:
        logger.warning("understanding the document failed for %s: %s", investigation_id, error)
        async with sessions() as session:
            await record_llm_calls(session, [*records, *getattr(error, "records", [])],
                                   investigation_id=investigation_id)
            await fail_investigation(session, investigation_id)
        return DocumentOutcome(investigation_id, error=f"{type(error).__name__}: {error}")

    output = structured.output
    idea = output.idea()
    facet_sources = {idea.facets[s.facet - 1].text: s.section for s in output.facet_sources}
    claims = [{"claim": c.claim, "section": c.section} for c in output.novelty_claims]
    verified = len(reading.verification.verified_quotes)
    async with sessions() as session:
        stored = await session.get(UserDocument, document.id)
        stored.reading = {"analysis": analysis, "extractor_version": reading.extractor_version,
                          "model": reading.model, "verified_quotes": verified,
                          "quotes": len(reading.verification.quotes)}
        stored.idea_extras = {"facet_sources": facet_sources, "novelty_claims": claims,
                              "schema_version": structured.schema_version, "prompt_version": structured.prompt_version}
        intake = await store_intake(
            session, investigation_id=investigation_id, mode=Mode.EVALUATE, agent_reading=idea.model_dump(mode="json"),
            schema_version=SCHEMA_VERSION, prompt_version=structured.prompt_version)
        await record_llm_calls(session, records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.AWAITING_CLARIFICATION,
                                    expected_from=Stage.UNDERSTANDING)
        intake_id = intake.id
    return DocumentOutcome(investigation_id, reading=idea, intake_id=intake_id, facet_sources=facet_sources,
                           novelty_claims=claims, reader_quotes=(verified, len(reading.verification.quotes)))
