"""The research package: the final deliverable for a strong candidate (doc 02 §5, doc 10 §7.7; step 7). ER region 5.

A ResearchPackage is grounded in ONE evaluation: if the candidate is evaluated again, an older package still points
to the evidence it was written from. `sections` holds what the Proposal Designer (A7) wrote, after code's checks:
each statement with its evidence status (evidence / interpretation / hypothesis / to_verify) and its sources as
claim and paper IDs. The sections that already exist as checked data (research question, novelty, closest works,
evidence, datasets, feasibility, references, the recommendation) are not copied in: they are rendered from that one
frozen evaluation whenever the package is shown (src/ai/workflows/proposal.py).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, Timestamps, UUIDPrimaryKey


class ResearchPackage(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "research_packages"
    __table_args__ = (UniqueConstraint("candidate_id", "revision"),)

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(128))
