"""The user's own research document (Mode 1; doc 02 §2.4, doc 10 §3.4; step 6a). ER region 1 (the request).

A UserDocument is the draft, proposal or paper a user uploads to have it evaluated. It is private to its
investigation and deleted with it. It is deliberately NOT stored in the shared library (Paper, Chunk,
PaperAnalysis, Quote are global): one forgotten filter there would leak unpublished work into other
investigations' searches.

  text, sections   the extracted text, split by its headings into sections D1, D2, ... (what the Paper Reader
                   reads, and what its quotes are verified against)
  consent_at       when the user agreed to send it to Claude for reading; nothing is read without it
  published,       the user's answer to "is this already published?", with a DOI / arXiv ID if given: used to
  external_ids     recognise the user's own paper in search results ("your own work", never prior art)
  reading          the Paper Reader's analysis of it (the same schema as for papers) with its quote checks.
                   Its quotes are labelled "your document" and are never evidence for a verdict.
  idea_extras      what the Idea Analyst added for the document: facet -> section links, novelty claims
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey


class UserDocument(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "user_documents"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    consent_at: Mapped[datetime] = mapped_column(AwareDateTime())
    published: Mapped[bool | None] = mapped_column(Boolean)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    reading: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idea_extras: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
