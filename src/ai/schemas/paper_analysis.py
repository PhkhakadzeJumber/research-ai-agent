"""The Paper Reader's output (agent A3; doc 04, doc 10 §5.3): a structured, idea-agnostic
reading of ONE paper.

What the model fills:
  - statements about the paper (research questions, findings, limitations, future work,
    assumptions), each with a VERBATIM quote and where it came from (abstract or chunk);
  - names as written in the paper (tasks, methods, datasets, metrics, baselines);
  - the setting and the contribution type.

What the model never fills: the paper ID, quote IDs, verification status, source level.
Code adds those after checking every quote against the stored text (src/ai/evidence).

Rules the schema can't enforce, so the verifier does: quotes must be verbatim, and
names must appear in the text. Rules the schema does enforce: no unknown fields, no
empty strings, closed sets for categories, and caps on list lengths and text sizes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "paper-analysis-v1"
"""Part of the extractor version stored with each analysis (a schema change = re-analysis)."""

MAX_ITEMS = 10
MAX_QUOTE_CHARS = 600
MAX_STATEMENT_CHARS = 300
MAX_NAME_CHARS = 120

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_STATEMENT_CHARS)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS)]

ContributionType = Literal[
    "method", "empirical_study", "dataset", "benchmark", "survey", "theory", "system", "position", "other", "unknown"
]
FindingDirection = Literal["positive", "negative", "no_effect", "mixed", "not_applicable"]


class _Strict(BaseModel):
    # Unknown fields are refused: the model can't add (or invent) fields we didn't ask for.
    model_config = ConfigDict(extra="forbid")


class EvidenceQuote(_Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUOTE_CHARS)] = Field(
        description=(
            "A passage copied EXACTLY, word for word, from the paper text you were given. Do not "
            "paraphrase, shorten with '...', fix typos or join separate sentences: a code check compares "
            "it with the stored text, and quotes that don't match are discarded. Keep it short: the "
            "smallest passage that supports the statement."
        )
    )
    chunk_id: str | None = Field(
        default=None,
        description=(
            "The ID of the full-text chunk the quote comes from, exactly as labelled in the input. "
            "Leave empty (null) if the quote comes from the abstract."
        ),
    )


class QuotedStatement(_Strict):
    statement: Text = Field(
        description="What the paper says, in one short sentence of your own words. Only what the paper states."
    )
    quote: EvidenceQuote = Field(description="The verbatim passage that states it.")


class Finding(_Strict):
    setting: Text = Field(
        description=(
            "The question or setting this result is about, specific enough to compare with other papers, "
            "e.g. 'RAG vs closed-book QA for 7B models on Swahili and Yoruba'."
        )
    )
    claim: Text = Field(description="The result the paper reports, in one short sentence.")
    direction: FindingDirection = Field(
        description=(
            "positive: the studied method/intervention helped; negative: it hurt; no_effect: no clear "
            "difference; mixed: depends on conditions; not_applicable: the finding isn't a comparison."
        )
    )
    quote: EvidenceQuote = Field(description="The verbatim passage that reports the result.")


class PaperAnalysisOutput(_Strict):
    """A structured reading of one paper. Fill only what the given text states; leave a list empty
    or a value null when the text doesn't say it. An empty field is correct; a guess is not."""

    research_questions: list[QuotedStatement] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="The questions or goals the paper itself states it investigates.",
    )
    tasks: list[Name] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Tasks addressed, named exactly as in the text (e.g. 'open-domain question answering').",
    )
    setting: Text | None = Field(
        default=None,
        description="Domain, population, language(s) or conditions the study covers, if stated; otherwise null.",
    )
    methods: list[Name] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Methods, models or techniques the paper uses or proposes, named exactly as in the text.",
    )
    datasets: list[Name] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Datasets or benchmarks used, named exactly as in the text. Never add ones you know from elsewhere.",
    )
    metrics: list[Name] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Evaluation metrics, named exactly as in the text.",
    )
    baselines: list[Name] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Systems or methods the paper compares against, named exactly as in the text.",
    )
    findings: list[Finding] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="The main results, each comparable across papers and backed by a verbatim quote.",
    )
    limitations: list[QuotedStatement] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description=(
            "Limitations the paper ITSELF admits (not your criticism). An abstract rarely states any: "
            "then leave this empty."
        ),
    )
    future_work: list[QuotedStatement] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Future work or open problems the paper itself proposes.",
    )
    assumptions: list[QuotedStatement] = Field(
        default_factory=list, max_length=MAX_ITEMS,
        description="Assumptions the paper states explicitly.",
    )
    contribution_type: ContributionType = Field(
        description=(
            "The paper's main kind of contribution. Use 'unknown' if the text doesn't make it clear."
        )
    )

    def quotes(self) -> Iterator[tuple[str, EvidenceQuote]]:
        """Every quote with its location, e.g. ('findings[1]', quote): what the verifier checks."""
        for field_name in ("research_questions", "findings", "limitations", "future_work", "assumptions"):
            for index, item in enumerate(getattr(self, field_name)):
                yield f"{field_name}[{index}]", item.quote

    def names(self) -> Iterator[tuple[str, str]]:
        """Every name with its location, e.g. ('datasets[0]', 'AfriQA'): checked to appear in the text."""
        for field_name in ("tasks", "methods", "datasets", "metrics", "baselines"):
            for index, name in enumerate(getattr(self, field_name)):
                yield f"{field_name}[{index}]", name
