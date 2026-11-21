"""Mined opportunity material for the Scout (Mode 2; doc 10 §6.2). Part of ER region 4
(where ideas come from).

An OpportunityItem is one pattern mined from the landscape's paper analyses (step 5.2b):
  subtopic            a subtopic of the field brief: its papers, publications per year
  limitation_cluster  a recurring open problem: future-work statements and negative/mixed
                      findings that point at the same problem (abstracts rarely state
                      limitations, so measured problems stand in for them)
  contradiction       two findings on the same question that point opposite ways
  combination         a method x problem hint (later: needs full text)
  dataset             a dataset named by the landscape papers, with their tasks

Every item reduces to verified quotes and papers (`quote_ids`, `paper_ids`). `counts` holds
only code-computed numbers (papers, author groups, per-year counts); `content` holds the
type's details. `prompt_version` is set when a model grouped or checked the item, and empty
for items built by code alone.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import one_of

OPPORTUNITY_TYPES = ("subtopic", "limitation_cluster", "contradiction", "combination", "dataset")


class OpportunityItem(UUIDPrimaryKey, Timestamps, Base):
    """One mined pattern, prepared for the Scout (doc 10 §6.2)."""

    __tablename__ = "opportunity_items"
    __table_args__ = (
        CheckConstraint(one_of("type", OPPORTUNITY_TYPES), name="type_valid"),
        Index("ix_opportunity_items_investigation_id_type", "investigation_id", "type"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(24))
    label: Mapped[str] = mapped_column(Text)
    quote_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list, server_default=sql_text("'{}'"))
    paper_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list, server_default=sql_text("'{}'"))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
