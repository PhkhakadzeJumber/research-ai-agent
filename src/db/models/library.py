"""The science library (ER region 3; field meanings in doc 10 §5).

Global knowledge, shared by every investigation: what papers exist, their text, the
idea-agnostic analysis of each one, and exact quotes. Nothing here is an opinion about
an idea, and nothing here is deleted when an investigation is deleted.

Integrity rules are enforced by the database itself (CHECK and UNIQUE constraints),
so even a bug in our code can't store an invalid paper or quote:
- a paper needs at least one external ID; tier is 1 or 2; plausible year; citations >= 0;
- one analysis per (paper, extractor version, source level);
- quotes are unique per (paper, chunk, text hash), also for abstract quotes (no chunk);
- a quote's source level matches whether it points at a chunk.

Embeddings (step 2.8): papers (title + abstract) and chunks have an `embedding` vector
and the `embedding_model` that made it; both are set together or not at all, and an
HNSW index makes cosine-similarity search fast. Vectors from different models are
never compared (doc 06 §2.4).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text  # renamed: several tables have a column called `text`
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, Timestamps, UUIDPrimaryKey
from src.retrieval.embeddings import EMBEDDING_DIMENSIONS

SOURCE_LEVELS = ("abstract", "full_text")
FULL_TEXT_STATUSES = ("none", "fetched", "parsed", "failed")

_WHITESPACE = re.compile(r"\s+")


def quote_text_hash(quote_text: str) -> str:
    """Fingerprint of a quote for deduplication: same words = same hash, regardless of
    line breaks, repeated spaces, case or Unicode form."""
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", quote_text)).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _embedding_rules(table: str) -> tuple:
    """Vector and model are set together, plus a cosine-similarity HNSW index."""
    return (
        CheckConstraint("(embedding IS NULL) = (embedding_model IS NULL)", name="embedding_has_model"),
        Index(f"ix_{table}_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )


class Paper(UUIDPrimaryKey, Timestamps, Base):
    """One scholarly work, deduplicated across sources. Agents cite papers only by `id`."""

    __tablename__ = "papers"
    __table_args__ = (
        UniqueConstraint("doi"),
        UniqueConstraint("arxiv_id"),
        UniqueConstraint("openalex_id"),
        UniqueConstraint("s2_id"),
        CheckConstraint(
            "doi IS NOT NULL OR arxiv_id IS NOT NULL OR openalex_id IS NOT NULL OR s2_id IS NOT NULL",
            name="has_external_id",
        ),
        CheckConstraint("tier IN (1, 2)", name="tier_valid"),
        CheckConstraint("year IS NULL OR year BETWEEN 1800 AND 2200", name="year_plausible"),
        CheckConstraint("citation_count IS NULL OR citation_count >= 0", name="citations_not_negative"),
        CheckConstraint(_one_of("full_text_status", FULL_TEXT_STATUSES), name="full_text_status_valid"),
        *_embedding_rules("papers"),
    )

    doi: Mapped[str | None] = mapped_column(String(255))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    openalex_id: Mapped[str | None] = mapped_column(String(32))
    s2_id: Mapped[str | None] = mapped_column(String(40))

    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=sql_text("'{}'"))
    citation_count: Mapped[int | None] = mapped_column(Integer)
    open_access_pdf_url: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(SmallInteger)
    full_text_status: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))  # of title + abstract
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    chunks: Mapped[list[Chunk]] = relationship(back_populates="paper", order_by="Chunk.position")
    analyses: Mapped[list[PaperAnalysis]] = relationship(back_populates="paper")


class Chunk(UUIDPrimaryKey, Timestamps, Base):
    """A passage of a paper's full text (~800 tokens). Immutable: a new chunking method
    creates a new set (new chunker_version) instead of rewriting, so quotes never break."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("paper_id", "chunker_version", "position"),
        CheckConstraint("position >= 0", name="position_not_negative"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        *_embedding_rules("chunks"),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    chunker_version: Mapped[str] = mapped_column(String(32))
    section_title: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Keyword-search column (the keyword half of hybrid search), maintained by Postgres itself.
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(section_title, '') || ' ' || text)", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))  # the vector half of hybrid search
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    paper: Mapped[Paper] = relationship(back_populates="chunks")


class PaperAnalysis(UUIDPrimaryKey, Timestamps, Base):
    """The Paper Reader's idea-agnostic analysis of one paper, cached and reused everywhere.

    `content` holds the structured fields (questions, methods, datasets, findings,
    limitations, future work, ...); its exact shape is the Paper Reader's output schema
    (step 3). Quote references inside it are Quote IDs.
    """

    __tablename__ = "paper_analyses"
    __table_args__ = (
        UniqueConstraint("paper_id", "extractor_version", "source_level"),
        CheckConstraint(_one_of("source_level", SOURCE_LEVELS), name="source_level_valid"),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    extractor_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    source_level: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)

    paper: Mapped[Paper] = relationship(back_populates="analyses")


class Quote(UUIDPrimaryKey, Timestamps, Base):
    """An exact piece of a paper's text that a statement relies on. Only the verifier
    (code, never an LLM) sets `verified` and `verification_score`."""

    __tablename__ = "quotes"
    __table_args__ = (
        # NULLS NOT DISTINCT: abstract quotes (chunk_id NULL) are deduplicated too.
        UniqueConstraint("paper_id", "chunk_id", "text_hash", postgresql_nulls_not_distinct=True),
        CheckConstraint(_one_of("source_level", SOURCE_LEVELS), name="source_level_valid"),
        CheckConstraint(
            "(chunk_id IS NULL AND source_level = 'abstract') OR (chunk_id IS NOT NULL AND source_level = 'full_text')",
            name="source_level_matches_chunk",
        ),
        CheckConstraint(
            "verification_score IS NULL OR (verification_score >= 0 AND verification_score <= 1)",
            name="verification_score_range",
        ),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunks.id"))
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    source_level: Mapped[str] = mapped_column(String(16))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    verification_score: Mapped[float | None] = mapped_column(Float)
