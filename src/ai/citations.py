"""Citations rendered from the database, never written by a model (CLAUDE.md: papers are referenced by internal ID).

Shared by the reports (scripts/report.py) and the research package (src/ai/workflows/proposal.py).
"""

from __future__ import annotations

from src.db.models import Paper


def paper_links(paper: Paper) -> dict[str, str | None]:
    """Every link the stored identifiers give: {"doi", "arxiv", "openalex"} (None where there's no identifier)."""
    return {"doi": f"https://doi.org/{paper.doi}" if paper.doi else None,
            "arxiv": f"https://arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else None,
            "openalex": f"https://openalex.org/{paper.openalex_id}" if paper.openalex_id else None}


def paper_link(paper: Paper) -> str:
    """The best single link: DOI, then arXiv, then OpenAlex ("" if none)."""
    links = paper_links(paper)
    return links["doi"] or links["arxiv"] or links["openalex"] or ""


def cite(paper: Paper) -> str:
    kind = "peer-reviewed" if paper.tier == 1 else "preprint"
    link = paper_link(paper)
    return f"*{paper.title}* ({paper.year or 'n.d.'}, {kind})" + (f" <{link}>" if link else "")
