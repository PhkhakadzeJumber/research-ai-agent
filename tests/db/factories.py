"""Small helpers that create valid rows for database tests."""

import uuid

from src.db.models import Evaluation
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.evaluations import create_evaluation


async def make_evaluation(session, investigation_id: uuid.UUID, *, depth="full", step="retrieving") -> Evaluation:
    """A real evaluation (with its candidate) in the investigation, so evaluation_id foreign keys hold."""
    candidate = await create_root_candidate(
        session, investigation_id=investigation_id, origin="user", title="An idea", research_question="Q?",
        facets=[FacetToStore("problem", "a problem", True, True)],
    )
    return await create_evaluation(session, candidate_id=candidate.id, depth=depth, step=step)
