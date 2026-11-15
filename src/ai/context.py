"""Context packets: exactly what an agent sees, built by code (doc 08 §5).

Agents never read the database themselves. The orchestrator gives each one a curated
packet, rendered as clearly labelled blocks so the model knows what it may cite:

    <paper id="..." year="2024" venue="ACL">
    <title>...</title>
    <authors>...</authors>
    <note>Only the abstract is available ...</note>
    <abstract>...</abstract>
    <chunk id="c_1" section="Limitations">...</chunk>
    </paper>

Paper text can't break out of its block: if it contains our tag names, they are
neutralized, so a malicious "</paper> new instructions" stays inside the data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ABSTRACT_ONLY_NOTE = (
    "Only the abstract of this paper is available. Analyse the abstract only, and never imply "
    "that you read the full paper."
)
FULL_TEXT_NOTE = "Passages from the full text are included as chunks, each with its ID and section."

_TAGS = re.compile(r"<(/?)(paper|title|authors|note|abstract|chunk)\b", re.IGNORECASE)
_QUOTE_IN_ATTRIBUTE = re.compile(r'["<>]')


@dataclass(frozen=True)
class ChunkForReading:
    chunk_id: str
    section_title: str | None
    text: str


@dataclass(frozen=True)
class PaperForReading:
    """The parts of a stored paper the Paper Reader is shown."""

    paper_id: str
    title: str
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    authors: tuple[str, ...] = ()
    chunks: tuple[ChunkForReading, ...] = field(default_factory=tuple)

    @property
    def source_level(self) -> str:
        """'full_text' if passages are included, otherwise 'abstract'."""
        return "full_text" if self.chunks else "abstract"


def _contain(text: str) -> str:
    """Neutralizes our tag names inside paper text ('</paper' -> '&lt;/paper')."""
    return _TAGS.sub(lambda match: f"&lt;{match.group(1)}{match.group(2)}", text)


def _attribute(value: str) -> str:
    return _QUOTE_IN_ATTRIBUTE.sub("", value)


def render_paper(paper: PaperForReading, *, max_authors: int = 10) -> str:
    """The Paper Reader's input for one paper. Raises ValueError if there's no text to read."""
    if not paper.abstract and not paper.chunks:
        raise ValueError(f"Paper {paper.paper_id} has neither an abstract nor full-text chunks to read.")

    attributes = f'id="{_attribute(paper.paper_id)}"'
    if paper.year is not None:
        attributes += f' year="{paper.year}"'
    if paper.venue:
        attributes += f' venue="{_attribute(paper.venue)}"'

    lines = [f"<paper {attributes}>", f"<title>{_contain(paper.title)}</title>"]
    if paper.authors:
        shown = list(paper.authors[:max_authors])
        if len(paper.authors) > max_authors:
            shown.append(f"and {len(paper.authors) - max_authors} more")
        lines.append(f"<authors>{_contain(', '.join(shown))}</authors>")
    lines.append(f"<note>{FULL_TEXT_NOTE if paper.chunks else ABSTRACT_ONLY_NOTE}</note>")
    if paper.abstract:
        lines.append(f"<abstract>\n{_contain(paper.abstract)}\n</abstract>")
    for chunk in paper.chunks:
        section = f' section="{_attribute(chunk.section_title)}"' if chunk.section_title else ""
        lines.append(f'<chunk id="{_attribute(chunk.chunk_id)}"{section}>\n{_contain(chunk.text)}\n</chunk>')
    lines.append("</paper>")
    return "\n".join(lines)
