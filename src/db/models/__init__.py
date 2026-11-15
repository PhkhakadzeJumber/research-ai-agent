"""All database tables (SQLAlchemy models), grouped by the six regions of docs/er-model.md.

Every model module must be imported here, so its tables are registered on
Base.metadata. Alembic (src/db/migrations/env.py) and the drift test rely on that:
a model that isn't imported here is invisible to migrations.
"""

from src.db.models.library import Chunk, Paper, PaperAnalysis, Quote  # region 3: science library
from src.db.models.run import Investigation, User  # region 1: request & run control
from src.db.models.search_trail import ApiCache, RetrievalHit, SearchQuery  # region 2: search trail

__all__ = [
    "ApiCache",
    "Chunk",
    "Investigation",
    "Paper",
    "PaperAnalysis",
    "Quote",
    "RetrievalHit",
    "SearchQuery",
    "User",
]
