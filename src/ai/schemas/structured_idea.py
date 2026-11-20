"""The Idea Analyst's reading of a Mode 1 idea (agent A1, task "structure idea"; doc 04, doc 10 §3.2).

What the model fills: a title, one answerable research question, the idea broken into
facets (problem / method / data_setting / evaluation / contribution), what it had to
assume, specific vagueness flags, and at most 3 clarifying questions, each with the
assumption Eureka uses if the user skips it.

Every facet says whether the user stated it or A1 proposed it to fill a gap
(stated_by_user). The title and research question stay in the user's terms; A1's
proposals live only in facets and assumptions, so they're visible and can be corrected.
Step 4.3 uses this: a key facet the user didn't state must be confirmed, not skipped.

Stored unchanged as the Intake's agent reading. When the user confirms (step 4.3), the
title, question and facets become Candidate v1 and its Facet rows.

The schema enforces the facet rules (every kind present, at most 2 per kind, at least
one key facet, no duplicates), so a broken answer goes back to the model through
run_structured's repair attempt instead of reaching the database.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "structured-idea-v2"  # v2: facets say whether the user stated them

FACET_KINDS = ("problem", "method", "data_setting", "evaluation", "contribution")
MAX_FACETS_PER_KIND = 2
MAX_QUESTIONS = 3
MAX_ASSUMPTIONS = 6
MAX_VAGUENESS_FLAGS = 5

FacetKind = Literal["problem", "method", "data_setting", "evaluation", "contribution"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Facet(_Strict):
    kind: FacetKind = Field(
        description=(
            "problem: what is wrong or unknown. method: the approach or technique. data_setting: the data, "
            "domain, population or language(s). evaluation: how success is measured (metrics, baselines). "
            "contribution: what the work would add to the field."
        )
    )
    text: Text = Field(description="The facet, specific enough to search for and compare with papers.")
    stated_by_user: bool = Field(
        description=(
            "True if the user's text states this component (in any wording). False if you proposed it to "
            "fill a gap; then also record the choice in assumptions."
        )
    )
    is_key: bool = Field(
        description=(
            "True if this facet is essential to what would make the idea new: changing it would make it a "
            "different idea. Usually the problem, the method and the data/setting; rarely the evaluation."
        )
    )


class ClarifyingQuestion(_Strict):
    question: Text = Field(description="One short question the user can answer in a sentence.")
    if_skipped: Text = Field(
        description="The assumption Eureka will use if the user skips this question, stated as a fact."
    )


class StructuredIdeaOutput(_Strict):
    """A precise, searchable reading of the user's research idea."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = Field(
        description="A short name for the idea in the user's own terms: never name a method or setting they didn't state."
    )
    research_question: Text = Field(
        description=(
            "ONE answerable research question, built only from what the user stated. Components you proposed "
            "go into facets and assumptions, not into the question."
        )
    )
    facets: list[Facet] = Field(
        max_length=len(FACET_KINDS) * MAX_FACETS_PER_KIND,  # "every kind" is checked below, with a clearer error
        description=(
            f"The idea broken into components: every kind at least once, at most {MAX_FACETS_PER_KIND} per "
            "kind, at least one key facet. If the idea doesn't state a component, propose a sensible one "
            "and record it in assumptions."
        ),
    )
    assumptions: list[Text] = Field(
        default_factory=list, max_length=MAX_ASSUMPTIONS,
        description="What you had to assume to fill gaps in the idea, each stated as a fact.",
    )
    vagueness_flags: list[Text] = Field(
        default_factory=list, max_length=MAX_VAGUENESS_FLAGS,
        description=(
            "Specific weaknesses in how the idea is stated, e.g. 'how hallucination is measured is "
            "unspecified', never just 'too vague'. Empty if the idea is clear."
        ),
    )
    clarifying_questions: list[ClarifyingQuestion] = Field(
        default_factory=list, max_length=MAX_QUESTIONS,
        description=(
            f"At most {MAX_QUESTIONS} questions whose answers would change the search or the judgement most. "
            "Empty if nothing important is unclear."
        ),
    )

    @model_validator(mode="after")
    def _facet_rules(self) -> StructuredIdeaOutput:
        counts = Counter(facet.kind for facet in self.facets)
        missing = [kind for kind in FACET_KINDS if not counts[kind]]
        if missing:
            raise ValueError(f"facets: every kind is needed; missing {', '.join(missing)}")
        too_many = [kind for kind, count in counts.items() if count > MAX_FACETS_PER_KIND]
        if too_many:
            raise ValueError(f"facets: at most {MAX_FACETS_PER_KIND} per kind; too many {', '.join(too_many)}")
        if not any(facet.is_key for facet in self.facets):
            raise ValueError("facets: mark at least one facet as key (is_key=true)")
        texts = [facet.text.casefold() for facet in self.facets]
        if len(set(texts)) != len(texts):
            raise ValueError("facets: each facet must be different")
        return self

    def facets_of(self, kind: str) -> list[Facet]:
        return [facet for facet in self.facets if facet.kind == kind]

    def proposed_key_facets(self) -> list[Facet]:
        """Key facets A1 proposed rather than read: the user must confirm these (step 4.3)."""
        return [facet for facet in self.facets if facet.is_key and not facet.stated_by_user]
