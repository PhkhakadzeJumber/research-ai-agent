"""baseline: enable the pgvector extension

Locally, infra/db/init/01-extensions.sql already enables pgvector when the Docker
database is first created. Cloud SQL never runs that script, so the first migration
enables it too. IF NOT EXISTS makes it safe to run on both.

Revision ID: 0001
Revises:
Create Date: 2026-11-15 14:40:37.518568+04:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
