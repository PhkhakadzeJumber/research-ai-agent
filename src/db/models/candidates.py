"""Where ideas come from (ER region 4; field meanings in doc 10 §6.3 and §6.5). Minimal for now.

A Candidate is a research idea under evaluation: in Mode 1 the user's own idea (v1,
created when the user confirms the Intake), in Mode 2 the Scout's ideas (step 5). Each
candidate is broken into Facets, which novelty is judged on.

Only the fields needed so far. The Scout's fields (origin strategy, motivation,
potential contribution, complexity fit, change note), inspirations and the current
evaluation arrive with the code that fills them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import CANDIDATE_STATUSES, one_of

# doc 10 §6.3: where a candidate came from
CANDIDATE_ORIGINS = ("user", "discover", "alternative", "refine", "replenish")

# doc 10 §6.5: the facet kinds (the Idea Analyst's schema uses the same names; a test compares them)
FACET_KINDS = ("problem", "method", "data_setting", "evaluation", "contribution")


class Candidate(UUIDPrimaryKey, Timestamps, Base):
    """A research idea under evaluation (doc 10 §6.3)."""

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(one_of("origin", CANDIDATE_ORIGINS), name="origin_valid"),
        CheckConstraint(one_of("status", CANDIDATE_STATUSES), name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        # a refinement continues its parent's lineage; the first version is its own root
        CheckConstraint("(version = 1) = (parent_id IS NULL)", name="parent_iff_refined"),
        UniqueConstraint("root_id", "version"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    # Refinement parent: this version IS the parent, changed to fix a stated problem.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    # Alternative source: a NEW idea suggested because another one failed (starts its own lineage).
    spawned_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    # The first version of this lineage ("all versions of this idea", refinements used so far).
    root_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    research_question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="proposed", server_default="proposed")
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class Facet(UUIDPrimaryKey, Timestamps, Base):
    """One component of a candidate's idea (doc 10 §6.5). Frozen with its candidate version."""

    __tablename__ = "facets"
    __table_args__ = (
        CheckConstraint(one_of("kind", FACET_KINDS), name="kind_valid"),
        UniqueConstraint("candidate_id", "position"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(SmallInteger)  # display order
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    is_key: Mapped[bool]
    # False: the Idea Analyst proposed it to fill a gap (the user confirmed it at clarification).
    stated_by_user: Mapped[bool]
