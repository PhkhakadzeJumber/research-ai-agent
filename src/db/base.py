"""The shared base for every database table.

- Base: all tables inherit from it, so they share one MetaData (which Alembic reads).
- Consistent constraint names (NAMING_CONVENTION), so migrations are predictable and
  a constraint can be found by name, e.g. "uq_papers_doi".
- AwareDateTime: a timestamptz column type that refuses naive datetimes, so the
  timestamp rule in CLAUDE.md is enforced when saving, not just written down.
- UUIDPrimaryKey / Timestamps: mixins for the id and created_at/updated_at columns
  most tables need.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from src import timeutil

# ix = index, uq = unique, ck = check, fk = foreign key, pk = primary key
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class AwareDateTime(TypeDecorator):
    """A `timestamp with time zone` column that only accepts timezone-aware datetimes.

    Postgres stores the instant; values read back are aware (in UTC). Convert with
    timeutil.to_iso() for display, which gives the Asia/Tbilisi offset.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is not None and isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("Naive datetime: every stored timestamp must carry a timezone offset.")
        return value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """An `id` column: a random UUID created in Python, so new rows have their id before saving."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamps:
    """created_at / updated_at, aware and set automatically."""

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), default=timeutil.now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), default=timeutil.now, server_default=func.now(), onupdate=timeutil.now, nullable=False
    )
