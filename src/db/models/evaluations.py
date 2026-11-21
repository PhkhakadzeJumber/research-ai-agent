"""Judging a candidate (ER region 5; field meanings in doc 10 §7.1). Minimal for now.

An Evaluation is one pass over one candidate version, at a depth (light or full). It
moves through the evaluation steps of the state graph (doc 05 §11.4) and records how it
ended (status). Its searches, hits and Claude calls point at it (evaluation_id).

Only the fields needed so far.

Step 4.6 adds the novelty evidence (doc 10 §7.2-7.3):
  FacetMatch     one cell of the prior-work matrix: how one paper relates to one facet
  Claim          one statement the reasoning relies on; its status is set by code only
  ClaimEvidence  a claim's link to a verified-or-not quote, with a stance
and two JSON columns on Evaluation: the Novelty Investigator's remaining output
(closest works, proposed verdict, uncertainties) and the coverage snapshot (frozen at
`concluding`). Step 4.7 adds Challenge: one objection raised by the Skeptic (doc 10 §7.4).
Step 4.9 adds Verdict (doc 10 §7.5: the frozen result per dimension, written at
`concluding`) and TriageDecision (doc 10 §7.6: what was decided about the candidate, and why).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src import timeutil
from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import EVALUATION_STATUSES, EVALUATION_STEPS, one_of

EVALUATION_DEPTHS = ("light", "full")

# doc 10 §7.2-7.3: the exact values
OVERLAPS = ("same", "similar", "different", "not_addressed", "unknown")
CLAIM_KINDS = ("prior_work", "gap", "significance", "feasibility", "limitation", "contradiction")
STATEMENT_TYPES = ("evidence", "interpretation", "hypothesis")
# System field, set by code only: proposed -> verified / unverified_quote / no_evidence -> challenged -> defended / refuted
CLAIM_STATUSES = ("proposed", "verified", "unverified_quote", "no_evidence", "challenged", "defended", "refuted")
AUTHOR_AGENTS = ("novelty", "skeptic", "value_feasibility")
STANCES = ("supports", "contradicts", "qualifies")
# doc 10 §7.4
CHALLENGE_TYPES = ("prior_work_found", "weak_assumption", "methodological_flaw", "overstated_contribution",
                   "alternative_explanation", "feasibility_risk")
CHALLENGE_RESOLUTIONS = ("unresolved", "accepted", "rebutted")
# doc 10 §7.5-7.6
VERDICT_DIMENSIONS = ("novelty", "significance", "feasibility", "recommendation")
TRIAGE_ACTIONS = ("advance", "refine", "reject", "expand_search", "finalize", "screen_out")


class Evaluation(UUIDPrimaryKey, Timestamps, Base):
    """One evaluation pass of a candidate version (doc 10 §7.1)."""

    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(one_of("depth", EVALUATION_DEPTHS), name="depth_valid"),
        CheckConstraint(one_of("step", EVALUATION_STEPS), name="step_valid"),
        CheckConstraint(one_of("status", EVALUATION_STATUSES), name="status_valid"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    # The light evaluation this full evaluation upgrades (it reuses the light pass's hits).
    extends_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluations.id"))
    depth: Mapped[str] = mapped_column(String(8))
    step: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=timeutil.now, server_default=sql_text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    # Queries, papers retrieved/unique, failed sources, ...: budgets and the coverage snapshot read these.
    counters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    # The Idea Analyst's query plan (src/ai/schemas/query_plan.py), with the facets each query covers.
    query_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The Novelty Investigator's output beyond matrix and claims: closest works (paper IDs),
    # proposed verdict, uncertainties (step 4.6).
    novelty_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The coverage snapshot (doc 10 §7.1): computed from the search trail, frozen at `concluding`.
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The Value & Feasibility Evaluator's assessment with code's checks (dataset availability,
    # timeline vs available time); its significance claims are Claim rows (step 4.8).
    value_feasibility: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FacetMatch(UUIDPrimaryKey, Timestamps, Base):
    """One cell of the prior-work matrix (doc 10 §7.2): how one paper relates to one facet."""

    __tablename__ = "facet_matches"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "facet_id", "paper_id"),
        CheckConstraint(one_of("overlap", OVERLAPS), name="overlap_valid"),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    facet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facets.id", ondelete="CASCADE"))
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"))
    overlap: Mapped[str] = mapped_column(String(16))
    # The evidence for the label. Without a verified quote, "same" doesn't count in verdicts.
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id"))
    rationale: Mapped[str] = mapped_column(Text)


class Claim(UUIDPrimaryKey, Timestamps, Base):
    """One statement Eureka's reasoning relies on (doc 10 §7.3). Text is never edited;
    only code moves the status."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(one_of("kind", CLAIM_KINDS), name="kind_valid"),
        CheckConstraint(one_of("statement_type", STATEMENT_TYPES), name="statement_type_valid"),
        CheckConstraint(one_of("status", CLAIM_STATUSES), name="status_valid"),
        CheckConstraint(one_of("author_agent", AUTHOR_AGENTS), name="author_agent_valid"),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    statement_type: Mapped[str] = mapped_column(String(16))
    inferred: Mapped[bool]
    status: Mapped[str] = mapped_column(String(16), default="proposed", server_default="proposed")
    author_agent: Mapped[str] = mapped_column(String(24))


class ClaimEvidence(UUIDPrimaryKey, Timestamps, Base):
    """A claim's link to one quote (doc 10 §7.3). Evidence can only be a Quote: signals live
    elsewhere, so "signals are never evidence" holds by structure."""

    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "quote_id"),
        CheckConstraint(one_of("stance", STANCES), name="stance_valid"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"))
    stance: Mapped[str] = mapped_column(String(16))



class Challenge(UUIDPrimaryKey, Timestamps, Base):
    """One objection raised by the Skeptic (doc 10 §7.4). Rounds are append-only: earlier
    challenges are never deleted. The resolution is set by code from the Novelty
    Investigator's response (step 4.7b)."""

    __tablename__ = "challenges"
    __table_args__ = (
        CheckConstraint(one_of("type", CHALLENGE_TYPES), name="type_valid"),
        CheckConstraint(one_of("resolution", CHALLENGE_RESOLUTIONS), name="resolution_valid"),
        CheckConstraint(one_of("statement_type", STATEMENT_TYPES), name="statement_type_valid"),
        CheckConstraint("round >= 1", name="round_positive"),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(SmallInteger)
    target_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id"))
    type: Mapped[str] = mapped_column(String(32))
    argument: Mapped[str] = mapped_column(Text)
    # evidence: backed by verified quotes; interpretation: an assumption/method critique without evidence
    statement_type: Mapped[str] = mapped_column(String(16))
    evidence_quote_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list,
                                                                server_default=sql_text("'{}'"))
    new_paper_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list, server_default=sql_text("'{}'"))
    # The facets (numbers) the Skeptic says a paper matches as "same": with verified quotes on every
    # key facet, the challenge overrides the novelty claim (doc 07 §4.5).
    same_on_facets: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default=sql_text("'{}'"))
    resolution: Mapped[str] = mapped_column(String(16), default="unresolved", server_default="unresolved")
    rebuttal_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id"))



class Verdict(UUIDPrimaryKey, Timestamps, Base):
    """The official conclusion for one dimension of an evaluation (doc 10 §7.5). Computed by code
    from stored evidence, written once at `concluding`, never changed afterwards."""

    __tablename__ = "verdicts"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "dimension"),
        CheckConstraint(one_of("dimension", VERDICT_DIMENSIONS), name="dimension_valid"),
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    rule: Mapped[str] = mapped_column(String(16))
    rule_trace: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    uncertainties: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    blocking_requirements: Mapped[list[str]] = mapped_column(JSONB, default=list,
                                                            server_default=sql_text("'[]'::jsonb"))
    rules_version: Mapped[str] = mapped_column(String(32))


class TriageDecision(UUIDPrimaryKey, Timestamps, Base):
    """What was decided about a candidate after an evaluation, and why (doc 10 §7.6)."""

    __tablename__ = "triage_decisions"
    __table_args__ = (
        CheckConstraint(one_of("action", TRIAGE_ACTIONS), name="action_valid"),
        CheckConstraint("rule_number BETWEEN 1 AND 9", name="rule_number_valid"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    # Empty for pre-screen decisions (Workflow B, step 5).
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    rule_number: Mapped[int] = mapped_column(SmallInteger)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    # The verdicts behind the decision: {"verdict_ids": [...]}
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    # The new version created by a `refine` decision (with the Scout, step 5).
    created_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
