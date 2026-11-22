"""Citations rendered from the database, never written by a model (CLAUDE.md: papers are referenced by internal ID).

Shared by the reports (scripts/report.py) and the research package (src/ai/workflows/proposal.py).
"""

from __future__ import annotations

from src.db.models import Paper


def paper_link(paper: Paper) -> str:
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    if paper.arxiv_id:
        return f"https://arxiv.org/abs/{paper.arxiv_id}"
    return f"https://openalex.org/{paper.openalex_id}" if paper.openalex_id else ""


def cite(paper: Paper) -> str:
    kind = "peer-reviewed" if paper.tier == 1 else "preprint"
    link = paper_link(paper)
    return f"*{paper.title}* ({paper.year or 'n.d.'}, {kind})" + (f" <{link}>" if link else "")
