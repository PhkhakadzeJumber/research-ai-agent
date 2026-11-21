"""The full report of an investigation (both workflows), built only from what is stored.

Usage (from the repo root, venv active, database running):
    python -m scripts.report <investigation id>
    python -m scripts.report <investigation id> --out report.md

No Claude calls: everything comes from the database (frozen verdicts if an evaluation has
concluded, otherwise the current ones, marked preliminary). Paper titles and links are
rendered from the stored records, never written by a model. Re-run it any time.

Mode 1 (evaluate an idea): the idea, its verdicts and evidence, then any refined versions and
alternatives. Mode 2 (find ideas, step 5.6b): the request, the field brief, the landscape and the
open problems mined from it, the ranked finalists (each with the same per-idea section as Mode 1),
the ideas that passed screening without a full evaluation, and every other idea with what happened
and why. The ranking is computed from the stored verdicts (src/ai/workflows/ranking.py).
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
from src.ai.workflows.landscape import confirmed_brief
from src.ai.workflows.ranking import Ranked, ranked_results
from src.ai.workflows.states import Mode
from src.ai.workflows.value_feasibility import verdicts_for
from src.config import ConfigError
from src.db.models import (
    Candidate,
    Challenge,
    Claim,
    Evaluation,
    Facet,
    Investigation,
    LlmCall,
    OpportunityItem,
    Paper,
    RetrievalHit,
    TriageDecision,
)
from src.db.repositories.evaluations import latest_evaluation
from src.db.session import Database

DIMENSIONS = ("novelty", "significance", "feasibility", "recommendation")
FOOTER = ("*Novelty verdicts are limited to the sources and papers searched: Eureka never claims an idea is "
          "original, only what it did and did not find.*")


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


async def facet_lines(session: AsyncSession, candidate: Candidate) -> list[str]:
    lines = []
    for facet in (await session.execute(select(Facet).where(Facet.candidate_id == candidate.id)
                                        .order_by(Facet.position))).scalars():
        key = " (key)" if facet.is_key else ""
        proposed = " [proposed by Eureka]" if not facet.stated_by_user and candidate.origin == "user" else ""
        lines.append(f"{facet.position + 1}. **{facet.kind.replace('_', ' ')}**{key}: {facet.text}{proposed}")
    return lines


async def candidate_section(session: AsyncSession, candidate: Candidate, evaluation: Evaluation, *, h: str = "##",
                            whose: str = "Your idea's", idea: str = "your idea") -> list[str]:
    """One evaluated idea: verdicts, decision, closest works, claims, Skeptic log, feasibility, coverage and
    limitations. `h` is the heading level ("##" in Mode 1, "####" under a Mode 2 finalist)."""
    frozen = await stored_verdicts(session, evaluation.id)
    live = await verdicts_for(session, evaluation.id)
    preliminary = "" if frozen else " (preliminary: the evaluation hasn't concluded)"
    depth = "" if evaluation.depth == "full" else " (light evaluation)"

    lines = ["", f"{h} Verdicts{depth}{preliminary}", ""]
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
                      f"{whose} status: {candidate.status.replace('_', ' ')}."]

    works = (evaluation.novelty_assessment or {}).get("closest_works", [])
    if works:
        lines += ["", f"{h} Closest works", ""]
        for work in works:
            paper = await session.get(Paper, uuid.UUID(work["paper_id"]))
            minor = " **Differs only in a minor facet.**" if work.get("minor_difference_only") else ""
            lines += [f"- {cite(paper)}", f"  - did: {work['what_they_did']}",
                      f"  - {idea} differs: {work['how_the_idea_differs']}{minor}"]

    claims = (await session.execute(select(Claim).where(Claim.evaluation_id == evaluation.id)
                                    .order_by(Claim.created_at))).scalars().all()
    if claims:
        lines += ["", f"{h} Claims (status set by code from verified quotes)", ""]
        for claim in claims:
            inferred = ", inferred" if claim.inferred else ""
            lines.append(f"- [{claim.status}] {claim.kind.replace('_', ' ')}{inferred}: {claim.text}")

    challenges = (await session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation.id)
                                        .order_by(Challenge.round, Challenge.created_at))).scalars().all()
    rounds = (evaluation.counters or {}).get("skeptic_rounds", [])
    if rounds:
        lines += ["", f"{h} Skeptic log", ""]
        for r in rounds:
            lines.append(f"- Round {r['round']}: {r.get('new_relevant', 0)} new relevant papers, "
                         f"{r.get('challenges', 0)} challenge(s)" + (" (saturated)" if r.get("saturated") else ""))
        for challenge in challenges:
            lines.append(f"  - round {challenge.round}, {challenge.type.replace('_', ' ')} "
                         f"({challenge.statement_type}) -> **{challenge.resolution}**: {challenge.argument}")

    value = evaluation.value_feasibility or {}
    if value and "error" not in value:
        feasibility = value["feasibility"]
        lines += ["", f"{h} Feasibility details", ""]
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

    coverage = evaluation.coverage or live.coverage
    funnel = coverage["funnel"]
    lines += ["", f"{h} Coverage" + ("" if evaluation.coverage else " (current, not yet frozen)"), "",
              f"{funnel['retrieved']} records -> {funnel['unique']} unique papers -> {funnel['screened']} screened -> "
              f"{funnel['relevant']} relevant -> {funnel['read']} read ({coverage['read_abstract_only']} from the "
              f"abstract only); sources: {', '.join(sorted(coverage['sources']))}; years "
              f"{coverage['years']['from']}-{coverage['years']['to']}", "", "Blind spots:"]
    lines.extend(f"- {spot}" for spot in coverage["blind_spots"])
    lines.extend(f"- budget: {hit}" for hit in coverage.get("budgets_hit", []))
    open_challenges = [c for c in challenges if c.resolution == "unresolved"]
    uncertainties = (evaluation.novelty_assessment or {}).get("uncertainties", [])
    if open_challenges or uncertainties:
        lines += ["", f"{h} Limitations", ""]
        lines.extend(f"- open challenge: {c.argument}" for c in open_challenges)
        lines.extend(f"- {item}" for item in uncertainties)
    return lines


async def cost_lines(session: AsyncSession, investigation_id: uuid.UUID) -> list[str]:
    rows = (await session.execute(
        select(LlmCall.role, func.count(), func.coalesce(func.sum(LlmCall.cost_usd), 0))
        .where(LlmCall.investigation_id == investigation_id).group_by(LlmCall.role).order_by(LlmCall.role)
    )).all()
    return ["", "## Cost", "", *(f"- {role.replace('_', ' ')}: {count} call(s), ${total:.2f}"
                                 for role, count, total in rows)]


def _verdict_summary(ranked: Ranked) -> str:
    s = ranked.standing
    parts = [f"{name} {LABELS.get(value, value)}" for name, value in
             (("recommendation", s.recommendation), ("novelty", s.novelty), ("feasibility", s.feasibility),
              ("significance", s.significance)) if value]
    return "; ".join(parts) + (f"; {s.supporting_quotes} verified supporting quotes" if s.supporting_quotes else "")


async def build_report(session: AsyncSession, investigation_id: uuid.UUID) -> str:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise LookupError(f"No investigation {investigation_id}.")
    if investigation.mode == Mode.DISCOVER:
        return await build_discovery_report(session, investigation)
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
    lines += await facet_lines(session, candidate)
    constraints = investigation.constraints_snapshot or {}
    if constraints:
        lines += ["", "Constraints: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in constraints.items() if v)]
    if investigation.assumptions:
        lines += ["", "Assumed: " + "; ".join(investigation.assumptions)]

    evaluation = (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate.id, Evaluation.depth == "full")
        .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none() \
        or await latest_evaluation(session, candidate.id)
    if evaluation is None:
        lines += ["", "The evaluation hasn't started yet."]
        return "\n".join(lines)
    lines += await candidate_section(session, candidate, evaluation)

    # refined versions and alternatives (steps 5.5a-5.6), ranked like Mode 2's ideas
    results = await ranked_results(session, investigation_id)
    others = [r for r in results.finalists + results.advanced if r.candidate.id != candidate.id]
    rest = [(c, why) for c, why in results.others if c.id != candidate.id]
    if others or rest:
        lines += ["", "## Refined versions and alternatives", ""]
        for r in others:
            kind = "finalist" if r.candidate.status == "finalist" else "passed screening, not fully evaluated"
            lines.append(f"- **{r.candidate.title}** (v{r.candidate.version}, {r.candidate.origin}; {kind}): "
                         f"{_verdict_summary(r)}")
        lines.extend(f"- {c.title} (v{c.version}, {c.origin}; {c.status.replace('_', ' ')}): {why}" for c, why in rest)

    lines += await cost_lines(session, investigation_id)
    lines += ["", FOOTER]
    return "\n".join(lines)


async def build_discovery_report(session: AsyncSession, investigation: Investigation) -> str:
    data = investigation.input or {}
    lines = [f"# Eureka report: research ideas in {data.get('field', 'your field')}", "",
             f"Investigation `{investigation.id}` | {investigation.stage} ({investigation.status}) | "
             f"complexity {investigation.complexity} | cost ${investigation.cost_usd:.2f}"]

    lines += ["", "## Your request", ""]
    for label, value in (("Field", data.get("field")), ("Interests", data.get("interests")),
                         ("Technologies", data.get("technologies")), ("Domain", data.get("domain")),
                         ("Exclusions", "; ".join(data.get("exclusions") or []) or None),
                         ("Ideas wanted", data.get("count"))):
        if value:
            lines.append(f"- {label}: {value}")
    constraints = investigation.constraints_snapshot or {}
    if constraints:
        lines.append("- Constraints: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in constraints.items() if v))

    try:
        brief = await confirmed_brief(session, investigation.id)
    except Exception:  # noqa: BLE001 - an unconfirmed brief just means no section
        brief = None
    items = list((await session.execute(select(OpportunityItem).where(
        OpportunityItem.investigation_id == investigation.id))).scalars())
    if brief is not None:
        subtopic_counts = {i.label: i.counts for i in items if i.type == "subtopic"}
        lines += ["", "## Field brief and landscape", ""]
        for s in brief.subtopics:
            counts = subtopic_counts.get(s.name, {})
            numbers = (f" ({counts.get('landscape_papers', 0)} papers found, {counts.get('read', 0)} read)"
                       if counts else "")
            lines.append(f"- **{s.name}**{numbers}: {s.why_it_fits_you}")
        found = (await session.execute(select(func.count()).select_from(RetrievalHit).where(
            RetrievalHit.investigation_id == investigation.id, RetrievalHit.evaluation_id.is_(None)))).scalar_one()
        read = (await session.execute(select(func.count()).select_from(RetrievalHit).where(
            RetrievalHit.investigation_id == investigation.id, RetrievalHit.evaluation_id.is_(None),
            RetrievalHit.selected_for_reading.is_(True)))).scalar_one()
        lines.append(f"\nLandscape: {found} unique papers found, {read} read closely.")

    problems = [i for i in items if i.type == "limitation_cluster"]
    contradictions = [i for i in items if i.type == "contradiction"]
    if problems or contradictions:
        lines += ["", "## Open problems found in the literature", ""]
        for item in sorted(problems, key=lambda i: -(i.counts or {}).get("papers", 0)):
            lines.append(f"- {item.label} ({item.counts.get('papers')} papers, {item.counts.get('author_groups')} "
                         "independent author groups)")
        lines.extend(f"- contradiction: {item.label}" for item in contradictions)

    results = await ranked_results(session, investigation.id)
    lines += ["", "## Ranked ideas", ""]
    if not results.finalists:
        lines.append("No idea completed a full evaluation as a finalist. The ideas below passed screening or were "
                     "set aside, each with the reason.")
    for n, ranked in enumerate(results.finalists, start=1):
        c = ranked.candidate
        notes = c.scout_notes or {}
        lines += ["", f"### {n}. {c.title}", "", f"**Research question:** {c.research_question}", "",
                  f"Why #{n}: {_verdict_summary(ranked)}"
                  + (f". Ranked above #{n + 1} on {ranked.ahead_because}." if ranked.ahead_because else ".")]
        if c.motivation:
            lines += ["", f"**Motivation:** {c.motivation}"]
        if notes:
            lines.append(f"**Fit:** {notes.get('compute_tier')}, about {notes.get('timeline_weeks')} weeks; data: "
                         f"{notes.get('data_source')}")
        lines += ["", *await facet_lines(session, c)]
        lines += await candidate_section(session, c, ranked.evaluation, h="####", whose="The idea's", idea="the idea")

    if results.advanced:
        lines += ["", "## Passed screening, not fully evaluated", ""]
        lines.extend(f"- **{r.candidate.title}**: {_verdict_summary(r)} (light evaluation)" for r in results.advanced)
    if results.others:
        lines += ["", "## Other ideas and why they stopped", ""]
        lines.extend(f"- {c.title} ({c.status.replace('_', ' ')}{', v' + str(c.version) if c.version > 1 else ''}): "
                     f"{why}" for c, why in results.others)

    lines += await cost_lines(session, investigation.id)
    lines += ["", FOOTER]
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
