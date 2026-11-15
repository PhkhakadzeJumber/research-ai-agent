"""The search trail (ER region 2; field meanings in doc 10 §4 and §8).

What Eureka searched, where, why, and what came back. The coverage snapshot is
computed from these rows, so every search leaves a row, including failed ones.

- SearchQuery: one query sent to one source, scoped to the landscape phase
  (evaluation_id empty) or to one evaluation.
- RetrievalHit: one paper found within one scope, with its relevance in that scope.
- ApiCache: stored API responses (replaces the temporary file cache).

evaluation_id has no foreign key yet: the evaluations table arrives in step 4, and
the migration that creates it adds the foreign keys here.

Deleting an investigation deletes its queries and hits (ON DELETE CASCADE), never papers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import one_of

QUERY_ORIGINS = ("analyst", "landscape", "scout", "skeptic")


class SearchQuery(UUIDPrimaryKey, Timestamps, Base):
    """One query sent to one source (doc 10 §4.1)."""

    __tablename__ = "search_queries"
    __table_args__ = (
        CheckConstraint(one_of("origin", QUERY_ORIGINS), name="origin_valid"),
        CheckConstraint("result_count >= 0", name="result_count_not_negative"),
        CheckConstraint("round IS NULL OR round >= 1", name="round_positive"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # FK added in step 4
    origin: Mapped[str] = mapped_column(String(16))
    query_type: Mapped[str | None] = mapped_column(String(32))
    round: Mapped[int | None] = mapped_column(Integer)  # Skeptic round, for saturation
    source: Mapped[str] = mapped_column(String(32))
    query_text: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class RetrievalHit(UUIDPrimaryKey, Timestamps, Base):
    """One paper found within one scope: the landscape phase or one evaluation (doc 10 §4.2)."""

    __tablename__ = "retrieval_hits"
    __table_args__ = (
        # One hit per paper per scope; NULLS NOT DISTINCT so the landscape scope
        # (evaluation_id empty) is covered too.
        UniqueConstraint("investigation_id", "evaluation_id", "paper_id", postgresql_nulls_not_distinct=True),
        CheckConstraint("llm_score IS NULL OR llm_score BETWEEN 0 AND 3", name="llm_score_range"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # FK added in step 4
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    found_by_query_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=sql_text("'{}'")
    )
    source_rank: Mapped[int | None] = mapped_column(Integer)  # best rank any source gave it (1 = top)
    embed_score: Mapped[float | None] = mapped_column(Float)
    llm_score: Mapped[int | None] = mapped_column(SmallInteger)
    llm_reason: Mapped[str | None] = mapped_column(Text)
    screened: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    selected_for_reading: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))


class ApiCache(Base):
    """A stored response from a literature API (doc 10 §8). Same rules as the file cache:
    secrets are never part of the key or the stored parameters."""

    __tablename__ = "api_cache"
    __table_args__ = (Index("ix_api_cache_source_fetched_at", "source", "fetched_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of source + path + public params
    source: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(AwareDateTime())
