"""Versioned prompt files and how they're assembled (doc 08 §4).

Each prompt is a Markdown file in this folder with a small header:

    ---
    name: paper_reader
    version: 1
    ---
    (the prompt text)

Every LLM call records the version of the prompts it used, so evaluation results can be
tied to exact prompt versions. A prompt change is a code change: bump the version.

System prompts are built as [shared preamble] + [role prompt]. They contain nothing
that changes between calls (no dates, no IDs), so they form a stable, cacheable prefix;
everything that varies goes into the user message (src/ai/context.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
SHARED_PREAMBLE = "shared_preamble"


class PromptError(ValueError):
    """A prompt file is missing or malformed."""


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def version_id(self) -> str:
        """e.g. 'paper_reader@1'."""
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    version_id: str
    """The versions of all parts, e.g. 'shared_preamble@1+paper_reader@1' (recorded with each LLM call)."""


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    """Reads src/ai/prompts/<name>.md and its header."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"Prompt file not found: {path.name}")
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")

    if not content.startswith("---\n") or "\n---\n" not in content[4:]:
        raise PromptError(f"{path.name}: missing '---' header with name and version")
    header, text = content[4:].split("\n---\n", 1)
    fields = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
    fields = {key.strip(): value.strip() for key, value in fields.items()}

    if fields.get("name") != name:
        raise PromptError(f"{path.name}: header name {fields.get('name')!r} doesn't match the file name")
    if not fields.get("version", "").isdigit():
        raise PromptError(f"{path.name}: header needs a whole-number version")
    text = text.strip()
    if not text:
        raise PromptError(f"{path.name}: empty prompt")
    return Prompt(name=name, version=int(fields["version"]), text=text)


def system_prompt(role: str, task: str | None = None) -> SystemPrompt:
    """The shared preamble followed by the role's prompt, or by the prompt of one of the
    role's tasks (file <role>_<task>.md, e.g. idea_analyst_revise.md)."""
    parts = [load_prompt(SHARED_PREAMBLE), load_prompt(f"{role}_{task}" if task else role)]
    return SystemPrompt(
        text="\n\n".join(part.text for part in parts),
        version_id="+".join(part.version_id for part in parts),
    )
