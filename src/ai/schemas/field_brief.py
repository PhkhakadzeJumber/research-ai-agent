"""The Idea Analyst's field brief (agent A1, task "field brief"; doc 04, doc 10 §3.2): the Mode 2
reading of a request to find a research idea.

What the model fills: 4-8 subtopics of the user's field worth exploring, each with why it
fits this user and 2-3 short keyword queries for the landscape search; what it had to
assume; and at most 3 clarifying questions, each with the assumption used if skipped.

The schema enforces what one brief can check on its own (counts, distinct subtopics,
short queries). The user's exclusions are checked by code against the request (a brief
that touches an excluded topic is rewritten once). Code, not the model, decides how each
query is searched (recent work, surveys, highly cited, newest preprints; step 5.1b).
Signal queries (GitHub, Hugging Face, news) come with the signals service.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.ai.schemas.structured_idea import MAX_ASSUMPTIONS, MAX_QUESTIONS, ClarifyingQuestion

SCHEMA_VERSION = "field-brief-v1"
MAX_QUERY_WORDS = 6
SUBTOPICS = (4, 8)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Subtopic(_Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    why_it_fits_you: Text = Field(
        description="Why this subtopic suits this user: interests, technologies, level, constraints, available data."
    )
    queries: list[Query] = Field(
        min_length=2, max_length=3,
        description=("2-3 keyword searches (2-6 words, most important first) that find this subtopic's literature. "
                     "No boolean operators or quotes."),
    )

    @model_validator(mode="after")
    def _short_queries(self) -> Subtopic:
        too_long = [q for q in self.queries if len(q.split()) > MAX_QUERY_WORDS]
        if too_long:
            raise ValueError(f"queries must have at most {MAX_QUERY_WORDS} words; shorten: {too_long}")
        return self


class FieldBriefOutput(_Strict):
    """Where in the user's field to look for a research idea."""

    subtopics: list[Subtopic] = Field(max_length=SUBTOPICS[1])
    assumptions: list[Text] = Field(default_factory=list, max_length=MAX_ASSUMPTIONS)
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list, max_length=MAX_QUESTIONS)

    @model_validator(mode="after")
    def _distinct_subtopics(self) -> FieldBriefOutput:
        if len(self.subtopics) < SUBTOPICS[0]:
            raise ValueError(f"subtopics: write {SUBTOPICS[0]}-{SUBTOPICS[1]} (you wrote {len(self.subtopics)})")
        names = [s.name.casefold() for s in self.subtopics]
        if len(set(names)) != len(names):
            raise ValueError("subtopics: each subtopic must be different")
        return self


def exclusion_problems(brief: FieldBriefOutput, exclusions: tuple[str, ...]) -> list[str]:
    """Subtopics or queries that mention something the user excluded (case-insensitive)."""
    problems = []
    for term in (t.strip().casefold() for t in exclusions if t.strip()):
        for subtopic in brief.subtopics:
            texts = [subtopic.name, *subtopic.queries]
            hits = [text for text in texts if term in text.casefold()]
            if hits:
                problems.append(f"subtopic {subtopic.name!r} mentions the excluded {term!r} ({'; '.join(hits)})")
    return problems


def violating_subtopics(brief: FieldBriefOutput, exclusions: tuple[str, ...]) -> list[str]:
    """Names of the subtopics that mention an excluded term."""
    terms = [t.strip().casefold() for t in exclusions if t.strip()]
    return [s.name for s in brief.subtopics
            if any(term in text.casefold() for term in terms for text in (s.name, *s.queries))]
