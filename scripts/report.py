"""The full report of an investigation (Workflow A), built only from what is stored.

Usage (from the repo root, venv active, database running):
    python -m scripts.report <investigation id>
    python -m scripts.report <investigation id> --out report.md

No Claude calls: everything comes from the database (frozen verdicts if the evaluation has
concluded, otherwise the current ones, marked preliminary). Paper titles and links are
rendered from the stored records, never written by a model. Re-run it any time.
Exit code: 0 ok, 2 unknown investigation or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.evidence.verdicts import LABELS
from src.ai.workflows.concluding import stored_verdicts
from src.ai.workflows.value_feasibility import verdicts_for
from src.config import ConfigError
from src.db.models import Candidate, Challenge, Claim, Facet, Investigation, LlmCall, Paper, TriageDecision
from src.db.repositories.evaluations import latest_evaluation
from src.db.session import Database

DIMENSIONS = ("novelty", "significance", "feasibility", "recommendation")


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


async def build_report(session: AsyncSession, investigation_id: uuid.UUID) -> str:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise LookupError(f"No investigation {investigation_id}.")
    candidate = (await session.execute(
        select(Candidate).where(Candidate.investigation_id == investigation_id).order_by(Candidate.created_at)
    )).scalars().first()
    lines = [f"# Eureka report: {candidate.title if candidate else investigation.input.get('idea', '')}", ""]
    lines.append(f"Investigation `{investigation.id}` | {investigation.stage} ({investigation.status}) | "
                 f"complexity {investigation.complexity} | cost ${investigation.cost_usd:.2f}")
    if candidate is None:
        lines += ["", "No confirmed idea yet: continue the investigation first."]
        return "\n".join(lines)

    lines += ["", "## Your idea", "", f"**Research question:** {candidate.research_question}", ""]
    for facet in (await session.execute(select(Facet).where(Facet.candidate_id == candidate.id)
                                        .order_by(Facet.position))).scalars():
        key = " (key)" if facet.is_key else ""
        proposed = " [proposed by Eureka]" if not facet.stated_by_user else ""
        lines.append(f"{facet.position + 1}. **{facet.kind.replace('_', ' ')}**{key}: {facet.text}{proposed}")
    constraints = investigation.constraints_snapshot or {}
    if constraints:
        lines += ["", "Constraints: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in constraints.items() if v)]
    if investigation.assumptions:
        lines += ["", "Assumed: " + "; ".join(investigation.assumptions)]

    evaluation = await latest_evaluation(session, candidate.id)
    if evaluation is None:
        lines += ["", "The evaluation hasn't started yet."]
        return "\n".join(lines)
    frozen = await stored_verdicts(session, evaluation.id)
    live = await verdicts_for(session, evaluation.id)
    preliminary = "" if frozen else " (preliminary: the evaluation hasn't concluded)"

    # --- verdicts ---
    lines += ["", f"## Verdicts{preliminary}", ""]
    for name in DIMENSIONS:
        if name in frozen:
            v = frozen[name]
            category, rule, trace, version, notes = v.category, v.rule, v.rule_trace, v.rules_version, v.uncertainties
        else:
            v = live.recommendation if name == "recommendation" else getattr(live, name)
            category, rule, trace, version = v.category, v.rule, v.trace, v.rules_version
            notes = getattr(v, "weaknesses", []) or (["needs further investigation"] if name == "recommendation"
                                                    and live.recommendation.needs_further_investigation else [])
        lines.append(f"- **{name.capitalize()}: {LABELS.get(category, category)}** ({version}, rule {rule})")
        lines.extend(f"  - {item}" for item in trace)
        lines.extend(f"  - limit: {item}" for item in notes)
    decision = (await session.execute(
        select(TriageDecision).where(TriageDecision.evaluation_id == evaluation.id)
        .order_by(TriageDecision.created_at.desc())
    )).scalars().first()
    if decision is not None:
        lines += ["", f"**Decision:** rule {decision.rule_number}: {decision.action} ({decision.reason}). "
                      f"Your idea's status: {candidate.status.replace('_', ' ')}."]

    # --- closest works ---
    works = (evaluation.novelty_assessment or {}).get("closest_works", [])
    if works:
        lines += ["", "## Closest works", ""]
        for work in works:
            paper = await session.get(Paper, uuid.UUID(work["paper_id"]))
            minor = " **Differs only in a minor facet.**" if work.get("minor_difference_only") else ""
            lines += [f"- {cite(paper)}", f"  - did: {work['what_they_did']}",
                      f"  - your idea differs: {work['how_the_idea_differs']}{minor}"]

    # --- claims ---
    claims = (await session.execute(select(Claim).where(Claim.evaluation_id == evaluation.id)
                                    .order_by(Claim.created_at))).scalars().all()
    if claims:
        lines += ["", "## Claims (status set by code from verified quotes)", ""]
        for claim in claims:
            inferred = ", inferred" if claim.inferred else ""
            lines.append(f"- [{claim.status}] {claim.kind.replace('_', ' ')}{inferred}: {claim.text}")

    # --- Skeptic log ---
    challenges = (await session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation.id)
                                        .order_by(Challenge.round, Challenge.created_at))).scalars().all()
    rounds = (evaluation.counters or {}).get("skeptic_rounds", [])
    if rounds:
        lines += ["", "## Skeptic log", ""]
        for r in rounds:
            lines.append(f"- Round {r['round']}: {r.get('new_relevant', 0)} new relevant papers, "
                         f"{r.get('challenges', 0)} challenge(s)" + (" (saturated)" if r.get("saturated") else ""))
        for challenge in challenges:
            lines.append(f"  - round {challenge.round}, {challenge.type.replace('_', ' ')} "
                         f"({challenge.statement_type}) -> **{challenge.resolution}**: {challenge.argument}")

    # --- feasibility details ---
    value = evaluation.value_feasibility or {}
    if value and "error" not in value:
        feasibility = value["feasibility"]
        lines += ["", "## Feasibility details", ""]
        for check in value.get("data_checks", []):
            lines.append(f"- data: {check['name']} -> {check['status'].replace('_', ' ')}")
        lines.append(f"- compute: {feasibility['compute_needed']}")
        timeline = value.get("timeline", {})
        lines.append(f"- timeline: {timeline.get('total_weeks')} weeks (available: {timeline.get('available_weeks')})")
        for title in ("skill_gaps", "risks", "ethics", "simplifications"):
            if feasibility.get(title):
                lines.append(f"- {title.replace('_', ' ')}:")
                lines.extend(f"  - {item}" for item in feasibility[title])
        significance = value.get("significance", {})
        if significance.get("beneficiaries"):
            lines.append(f"- who benefits: {'; '.join(significance['beneficiaries'])}")

    # --- coverage and limitations ---
    coverage = evaluation.coverage or live.coverage
    funnel = coverage["funnel"]
    lines += ["", "## Coverage" + ("" if evaluation.coverage else " (current, not yet frozen)"), "",
              f"{funnel['retrieved']} records -> {funnel['unique']} unique papers -> {funnel['screened']} screened -> "
              f"{funnel['relevant']} relevant -> {funnel['read']} read ({coverage['read_abstract_only']} from the "
              f"abstract only); sources: {', '.join(sorted(coverage['sources']))}; years "
              f"{coverage['years']['from']}-{coverage['years']['to']}", "", "Blind spots:"]
    lines.extend(f"- {spot}" for spot in coverage["blind_spots"])
    lines.extend(f"- budget: {hit}" for hit in coverage.get("budgets_hit", []))
    open_challenges = [c for c in challenges if c.resolution == "unresolved"]
    uncertainties = (evaluation.novelty_assessment or {}).get("uncertainties", [])
    if open_challenges or uncertainties:
        lines += ["", "## Limitations", ""]
        lines.extend(f"- open challenge: {c.argument}" for c in open_challenges)
        lines.extend(f"- {item}" for item in uncertainties)

    # --- cost ---
    rows = (await session.execute(
        select(LlmCall.role, func.count(), func.coalesce(func.sum(LlmCall.cost_usd), 0))
        .where(LlmCall.investigation_id == investigation_id).group_by(LlmCall.role).order_by(LlmCall.role)
    )).all()
    lines += ["", "## Cost", ""]
    lines.extend(f"- {role.replace('_', ' ')}: {count} call(s), ${total:.2f}" for role, count, total in rows)
    lines += ["", "*Novelty verdicts are limited to the sources and papers searched: Eureka never claims an idea is "
                  "original, only what it did and did not find.*"]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.report", description="Print an investigation's report.")
    parser.add_argument("investigation", type=uuid.UUID)
    parser.add_argument("--out", type=Path, help="also write the report to this Markdown file")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    try:
        database = Database.from_settings()
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    try:
        if not await database.ping():
            print("Error: the database isn't reachable: start it with `docker compose up -d db`.", file=sys.stderr)
            return 2
        async with database.session() as session:
            try:
                report = await build_report(session, args.investigation)
            except LookupError as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
        print(report)
        if args.out:
            args.out.write_text(report + "\n", encoding="utf-8")
            print(f"\n(written to {args.out})")
        return 0
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
