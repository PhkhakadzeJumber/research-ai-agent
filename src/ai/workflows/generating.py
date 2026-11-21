"""Which Scout task runs at stage `generating_candidates` (doc 05 §11.1), both workflows.

generate_step(sessions, investigation_id) -> (task, outcome):
  - Mode 1: alternatives to the user's rejected idea (src/ai/workflows/alternatives.py);
  - Mode 2 without candidates yet: discover, the first 8-12 ideas (step 5.3);
  - Mode 2 with candidates: replenish, the reserve first, then one Scout round (step 5.5b).
All three end at `prescreening`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from src.ai.workflows.alternatives import alternatives_step
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.scouting import ScoutingOutcome, at_generating, replenish_step, scouting_step
from src.ai.workflows.states import Mode
from src.db.models import Candidate


async def generate_step(sessions: Sessions, investigation_id: uuid.UUID, *, settings=None,
                        model=None) -> tuple[str, ScoutingOutcome]:
    async with sessions() as session:
        investigation = await at_generating(session, investigation_id)
        mode = investigation.mode
        existing = (await session.execute(select(func.count()).select_from(Candidate).where(
            Candidate.investigation_id == investigation_id))).scalar_one()
    if mode == Mode.EVALUATE:
        return "alternatives", await alternatives_step(sessions, investigation_id, settings=settings, model=model)
    if existing == 0:
        return "discover", await scouting_step(sessions, investigation_id, settings=settings, model=model)
    return "replenish", await replenish_step(sessions, investigation_id, settings=settings, model=model)
