"""All database tables (SQLAlchemy models), grouped by the six regions of docs/er-model.md.

Every model module must be imported here, so its tables are registered on
Base.metadata. Alembic (src/db/migrations/env.py) and the drift test rely on that:
a model that isn't imported here is invisible to migrations.
"""

from src.db.models.accounts import EmailToken, UserSession  # region 1: login sessions and email links
from src.db.models.billing import CreditGrant, CreditHold  # region 1: plans and credits
from src.db.models.documents import UserDocument  # region 1: the user's own document
from src.db.models.candidates import Candidate, CandidateInspiration, Facet  # region 4: where ideas come from
from src.db.models.evaluations import (  # region 5: judging a candidate
    Challenge,
    Claim,
    ClaimEvidence,
    Evaluation,
    FacetMatch,
    TriageDecision,
    Verdict,
)
from src.db.models.packages import ResearchPackage  # region 5: the final deliverable
from src.db.models.progress import ProgressEvent  # region 1: what a run did, for the progress screen
from src.db.models.library import Chunk, Paper, PaperAnalysis, Quote  # region 3: science library
from src.db.models.opportunities import OpportunityItem  # region 4: mined material for the Scout
from src.db.models.run import Intake, Investigation, LlmCall, User  # region 1: request & run control
from src.db.models.search_trail import ApiCache, RetrievalHit, SearchQuery  # region 2: search trail

__all__ = [
    "EmailToken",
    "CreditGrant",
    "CreditHold",
    "UserSession",
    "ProgressEvent",
    "ApiCache",
    "Candidate",
    "CandidateInspiration",
    "Challenge",
    "Chunk",
    "Claim",
    "ClaimEvidence",
    "Evaluation",
    "Facet",
    "FacetMatch",
    "Intake",
    "Investigation",
    "LlmCall",
    "OpportunityItem",
    "Paper",
    "PaperAnalysis",
    "Quote",
    "ResearchPackage",
    "RetrievalHit",
    "SearchQuery",
    "TriageDecision",
    "User",
    "UserDocument",
    "Verdict",
]
