"""The quote verifier: does a quote really appear, word for word, where the model says it does?

Pure code (doc 07 §2, guarantee 2). An agent can PROPOSE a quote; only this check can mark
it verified, and unverified quotes never count in verdicts.

Tolerated, because they're formatting, not content:
  - line breaks, repeated spaces, upper/lower case;
  - words hyphenated across a line break ("evalu-\\nation" = "evaluation");
  - curly vs straight quotes, different dashes, ligatures ("ﬁ" = "fi"), soft hyphens;
  - quote marks or trailing punctuation the model put around the quote.
Not tolerated, because they change what was said:
  - changed, added or removed words (beyond a ~5% character difference: similarity < 0.95);
  - passages joined with "..." / "…";
  - a quote checked against the wrong place (the abstract vs a chunk);
  - an added or removed negation ("not", "never", "no", "n't", ...), even in an otherwise
    near-identical sentence, because "is effective" and "is not effective" can be more than
    95% similar and say the opposite.

Matching: exact match after normalization (score 1.0), otherwise the most similar passage
of the same length (fuzzy, accepted at >= 0.95 with identical negations).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from src.ai.schemas.paper_analysis import PaperAnalysisOutput

SIMILARITY_THRESHOLD = 0.95
MIN_ANCHOR_CHARS = 8  # a shared run this long is needed to look for a near-match at all

MatchMethod = Literal["exact", "fuzzy", "none"]

_QUOTE_CHARS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
}
_DASH_CHARS = {c: "-" for c in "‐‑‒–—―−"}
_TRANSLATION = str.maketrans({**_QUOTE_CHARS, **_DASH_CHARS, "­": ""})  # ­: soft hyphen

_LINE_BREAK_HYPHEN = re.compile(r"(\w)-[ \t]*\r?\n\s*(\w)")
_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = " \"'.,;:"
_ELLIPSIS = re.compile(r"\.\.\.|…")
_NEGATIONS = re.compile(r"\b(?:not|no|never|none|nor|neither|nothing|nobody|nowhere|without|cannot)\b|n't\b")


def normalize(text: str) -> str:
    """The comparison form of a text: same words, formatting differences removed."""
    # "&lt;" -> "<": paper text shown to agents has tag-like text neutralized (src/ai/context.py),
    # so a quote of such a passage must still match the stored original.
    text = unicodedata.normalize("NFKC", text).replace("&lt;", "<").translate(_TRANSLATION)
    text = _LINE_BREAK_HYPHEN.sub(r"\1\2", text)  # "evalu-\nation" -> "evaluation" (before newlines vanish)
    return _WHITESPACE.sub(" ", text).strip().lower()


def _negations(text: str) -> list[str]:
    return sorted(_NEGATIONS.findall(text))


@dataclass(frozen=True)
class QuoteMatch:
    verified: bool
    score: float
    """Similarity with the best-matching passage, 0..1 (stored as quotes.verification_score)."""
    method: MatchMethod
    reason: str | None = None
    """Why the quote failed, in plain words (None when verified)."""


def match_quote(quote: str, source: str | None) -> QuoteMatch:
    """Checks one quote against one source text (an abstract or a chunk)."""
    if not source:
        return QuoteMatch(False, 0.0, "none", "no source text to check against")
    if _ELLIPSIS.search(quote):
        return QuoteMatch(False, 0.0, "none", "contains an ellipsis: quotes must be one continuous passage")

    needle = normalize(quote).strip(_EDGE_PUNCTUATION)
    haystack = normalize(source)
    if not needle:
        return QuoteMatch(False, 0.0, "none", "empty quote")
    if needle in haystack:
        return QuoteMatch(True, 1.0, "exact")

    score, passage = _best_passage(needle, haystack)
    if score < SIMILARITY_THRESHOLD:
        return QuoteMatch(False, round(score, 4), "none", f"not found (best similarity {score:.2f})")
    if _negations(needle) != _negations(passage):
        return QuoteMatch(False, round(score, 4), "none", "a negation differs from the source text")
    return QuoteMatch(True, round(score, 4), "fuzzy")


def _best_passage(needle: str, haystack: str) -> tuple[float, str]:
    """The passage of `haystack` most similar to `needle`, found around shared runs of text."""
    matcher = difflib.SequenceMatcher(None, haystack, needle, autojunk=False)
    best_score, best_passage = 0.0, ""
    for block in matcher.get_matching_blocks():
        if block.size < MIN_ANCHOR_CHARS:
            continue
        start = max(block.a - block.b, 0)  # where the needle would start if this run lines up
        for offset in (-3, 0, 3):  # small shifts absorb a few inserted/removed characters
            begin = max(start + offset, 0)
            for length in (len(needle) - 3, len(needle), len(needle) + 3):
                passage = haystack[begin:begin + max(length, 1)]
                score = difflib.SequenceMatcher(None, needle, passage, autojunk=False).ratio()
                if score > best_score:
                    best_score, best_passage = score, passage
    return best_score, best_passage


# --- whole analyses ------------------------------------------------------------------------------

@dataclass(frozen=True)
class QuoteCheck:
    location: str
    """Where the quote is in the analysis, e.g. 'findings[1]'."""
    text: str
    chunk_id: str | None
    verified: bool
    score: float
    method: MatchMethod
    reason: str | None


@dataclass(frozen=True)
class NameCheck:
    location: str
    name: str
    found: bool


@dataclass(frozen=True)
class VerificationReport:
    quotes: tuple[QuoteCheck, ...]
    names: tuple[NameCheck, ...]

    @property
    def verified_quotes(self) -> tuple[QuoteCheck, ...]:
        return tuple(check for check in self.quotes if check.verified)

    @property
    def unverified_quotes(self) -> tuple[QuoteCheck, ...]:
        return tuple(check for check in self.quotes if not check.verified)

    @property
    def missing_names(self) -> tuple[NameCheck, ...]:
        return tuple(check for check in self.names if not check.found)

    @property
    def verification_rate(self) -> float | None:
        """Share of quotes verified (None if there were no quotes). Target: >= 95% (doc 13)."""
        return len(self.verified_quotes) / len(self.quotes) if self.quotes else None


def _name_form(text: str) -> str:
    # For names only, hyphens count as spaces: "Retrieval-Augmented" == "Retrieval Augmented".
    return _WHITESPACE.sub(" ", normalize(text).replace("-", " ")).strip()


def verify_analysis(
    analysis: PaperAnalysisOutput, *, abstract: str | None, chunks: Mapping[str, str] | None = None
) -> VerificationReport:
    """Checks every quote at the place it claims to come from, and every name in the whole text.

    Args:
        abstract: the paper's abstract (quotes with no chunk_id are checked against it).
        chunks: chunk ID -> chunk text, for full-text quotes.
    """
    chunks = chunks or {}
    quote_checks = []
    for location, quote in analysis.quotes():
        if quote.chunk_id is None:
            source = abstract
            if not abstract:
                match = QuoteMatch(False, 0.0, "none", "cites the abstract, but the paper has no abstract")
            else:
                match = match_quote(quote.text, source)
        elif quote.chunk_id not in chunks:
            match = QuoteMatch(False, 0.0, "none", f"cites unknown chunk {quote.chunk_id!r}")
        else:
            match = match_quote(quote.text, chunks[quote.chunk_id])
        quote_checks.append(QuoteCheck(location, quote.text, quote.chunk_id, match.verified, match.score,
                                       match.method, match.reason))

    full_text = _name_form(" ".join([abstract or "", *chunks.values()]))
    name_checks = [NameCheck(location, name, _name_form(name) in full_text) for location, name in analysis.names()]
    return VerificationReport(quotes=tuple(quote_checks), names=tuple(name_checks))
