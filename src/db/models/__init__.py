"""All database tables (SQLAlchemy models), grouped by the six regions of docs/er-model.md.

Every model module must be imported here, so its tables are registered on
Base.metadata. Alembic (src/db/migrations/env.py) and the drift test rely on that:
a model that isn't imported here is invisible to migrations.
"""

from src.db.models.library import Chunk, Paper, PaperAnalysis, Quote

__all__ = ["Chunk", "Paper", "PaperAnalysis", "Quote"]
