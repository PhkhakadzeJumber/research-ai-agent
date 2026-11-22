"""The research package (A7; doc 02 §5, doc 04, doc 05 §11.1 `writing_proposal`, doc 10 §7.7; step 7).

write_proposal(sessions, investigation_id, candidate_id=None) at stage `results_ready`:
  1. the candidate: the one given, or the top-ranked finalist (src/ai/workflows/ranking.py); it moves
     `finalist -> selected`, and the investigation `results_ready -> writing_proposal`;
  2. code collects what its latest concluded evaluation found, labelled for the model: the frozen verdicts, the
     claims (C1, ...) with their verified quotes, the closest works and facet matrix, the Skeptic's challenges, the
     feasibility details, and the papers (P1, ...) with the methods, datasets, metrics and baselines they use;
  3. A7 writes the package's written sections (src/ai/agents/proposal_designer.py);
  4. code checks every statement (doc 04's rules): unknown labels are dropped, so no reference can come from
     outside the evidence ledger; an "evidence" statement must cite a supported claim (verified or defended) or a
     paper with a verified quote, otherwise it becomes "to_verify"; paper labels in the text become titles;
  5. the package is stored (one evaluation, revision 1, 2, ...), the candidate becomes `proposal_ready` and the
     investigation returns to `results_ready`.
package_sections(session, package) assembles all 17 sections for display: A7's sections plus the ones code renders
from that one frozen evaluation (research question, novelty, closest works, evidence, datasets, feasibility,
references, the recommendation) and the Skeptic's unresolved challenges, which always appear under limitations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import false, func, select

from src.ai.agents.proposal_designer import design_proposal
from src.ai.citations import cite
from src.ai.context import ClaimForProposal, PaperForProposal, ProposalRequest, name_papers
from src.ai.evidence.verdicts import LABELS
from src.ai.workflows.concluding import stored_verdicts
from src.ai.workflows.evaluation import Sessions, candidate_for_planning
from src.ai.workflows.ranking import ranked_results
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status
from src.config import RoleLlmSettings
from src.db.models import (
    Candidate,
    Challenge,
    Claim,
    ClaimEvidence,
    Evaluation,
    Facet,
    FacetMatch,
    Investigation,
    Paper,
    PaperAnalysis,
    Quote,
    ResearchPackage,
)
from src.db.repositories.llm_calls import record_llm_calls

SUPPORTED_CLAIMS = ("verified", "defended")
WRITTEN = ("background", "motivation", "contribution", "methodology", "hypotheses", "tools", "experiment_design",
           "limitations")
MAX_PAPERS = 25
TYPE_WORDS = {"evidence": "evidence", "interpretation": "interpretation", "hypothesis": "hypothesis",
              "to_verify": "to verify"}


class NoCandidateForProposal(ValueError):
    """Nothing to write a package for: no finalist, or the candidate given can't have one."""


@dataclass
class ProposalOutcome:
    package: ResearchPackage
    candidate: Candidate
    retagged: int = 0
    dropped_labels: int = 0
    records: list = field(default_factory=list)


async def _evaluation_of(session, candidate: Candidate) -> Evaluation:
    evaluation = (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate.id, Evaluation.status.in_(("done", "partial")))
        .order_by((Evaluation.depth == "full").desc(), Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()
    if evaluation is None:
        raise NoCandidateForProposal(f"'{candidate.title}' has no concluded evaluation to build a package on.")
    return evaluation


async def choose_candidate(session, investigation_id: uuid.UUID, candidate_id: uuid.UUID | None) -> Candidate:
    if candidate_id is not None:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None or candidate.investigation_id != investigation_id:
            raise NoCandidateForProposal(f"No candidate {candidate_id} in this investigation.")
        if candidate.status not in (CandidateStatus.FINALIST, CandidateStatus.SELECTED, CandidateStatus.PROPOSAL_READY):
            raise NoCandidateForProposal(f"'{candidate.title}' is {candidate.status.replace('_', ' ')}: a package is "
                                         "written for a finalist.")
        return candidate
    results = await ranked_results(session, investigation_id)
    if not results.finalists:
        raise NoCandidateForProposal("This investigation has no finalist to write a package for.")
    return results.finalists[0].candidate


async def build_request(session, investigation: Investigation, candidate: Candidate, evaluation: Evaluation):
    """The model's packet, plus label -> claim, label -> paper, the supported labels, and label -> Paper (for titles)."""
    claims = list((await session.execute(select(Claim).where(Claim.evaluation_id == evaluation.id)
                                         .order_by(Claim.created_at))).scalars())
    evidence = (await session.execute(
        select(ClaimEvidence, Quote).join(Quote, Quote.id == ClaimEvidence.quote_id)
        .where(ClaimEvidence.claim_id.in_([c.id for c in claims]) if claims else false(), Quote.verified.is_(True))
    )).all()
    matches = list((await session.execute(select(FacetMatch).where(FacetMatch.evaluation_id == evaluation.id))).scalars())
    assessment = evaluation.novelty_assessment or {}
    works = list(assessment.get("closest_works", []))

    paper_ids = list(dict.fromkeys([
        *(uuid.UUID(w["paper_id"]) for w in works), *(uuid.UUID(p) for p in assessment.get("papers_examined", [])),
        *(q.paper_id for _, q in evidence), *(m.paper_id for m in matches)]))[:MAX_PAPERS]
    papers = {p.id: p for p in (await session.execute(select(Paper).where(Paper.id.in_(paper_ids)))).scalars()} \
        if paper_ids else {}
    p_label = {pid: f"P{n}" for n, pid in enumerate((pid for pid in paper_ids if pid in papers), start=1)}
    verified_papers = {q.paper_id for _, q in evidence} | {m.paper_id for m in matches if m.quote_id}
    if any(m.quote_id for m in matches):
        verified_quotes = set((await session.execute(select(Quote.id).where(
            Quote.id.in_([m.quote_id for m in matches if m.quote_id]), Quote.verified.is_(True)))).scalars())
        verified_papers = {q.paper_id for _, q in evidence} | {m.paper_id for m in matches if m.quote_id in verified_quotes}

    quotes_by_claim: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for ce, quote in evidence:
        if quote.paper_id in p_label:
            quotes_by_claim.setdefault(ce.claim_id, []).append((p_label[quote.paper_id], quote.text))
    c_label = {c.id: f"C{n}" for n, c in enumerate(claims, start=1)}
    facet_number = {f.id: f.position + 1 for f in (await session.execute(
        select(Facet).where(Facet.candidate_id == candidate.id))).scalars()}

    analyses = {}
    for pid in p_label:
        analysis = (await session.execute(select(PaperAnalysis).where(PaperAnalysis.paper_id == pid)
                                          .order_by(PaperAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
        analyses[pid] = (analysis.content if analysis else {}) or {}
    frozen = await stored_verdicts(session, evaluation.id)
    challenges = list((await session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation.id)
                                             .order_by(Challenge.round, Challenge.created_at))).scalars())
    value = evaluation.value_feasibility or {}
    feasibility = value.get("feasibility") or {}
    timeline = value.get("timeline") or {}
    feas_lines = [*(f"data: {d['name']} ({d['status'].replace('_', ' ')})" for d in value.get("data_checks", [])),
                  *([f"compute: {feasibility['compute_needed']}"] if feasibility.get("compute_needed") else []),
                  *([f"timeline: {timeline.get('total_weeks')} weeks of {timeline.get('available_weeks')} available"]
                    if timeline else []),
                  *(f"blocking: {b}" for b in [*feasibility.get("blocking_requirements", []), *value.get("code_blocking", [])]),
                  *(f"risk: {r}" for r in feasibility.get("risks", [])),
                  *(f"simplification: {s}" for s in feasibility.get("simplifications", []))]
    request = ProposalRequest(
        candidate=await candidate_for_planning(session, candidate),
        verdicts=tuple(f"{d}: {LABELS.get(v.category, v.category)} (rule {v.rule}"
                       + (f": {v.rule_trace[0]}" if v.rule_trace else "") + ")"
                       for d, v in sorted(frozen.items(), key=lambda kv: ("novelty", "significance", "feasibility",
                                                                          "recommendation").index(kv[0]))),
        claims=tuple(ClaimForProposal(c_label[c.id], c.kind, c.status, c.text, tuple(quotes_by_claim.get(c.id, [])[:2]))
                     for c in claims),
        closest_works=tuple((p_label[uuid.UUID(w["paper_id"])], bool(w.get("minor_difference_only")),
                             w.get("what_they_did", ""), w.get("how_the_idea_differs", ""))
                            for w in works if uuid.UUID(w["paper_id"]) in p_label),
        cells=tuple((p_label[m.paper_id], facet_number.get(m.facet_id, 0), m.overlap) for m in matches
                    if m.paper_id in p_label),
        challenges=tuple((f"K{n}", ch.type, ch.resolution, ch.argument) for n, ch in enumerate(challenges, start=1)),
        feasibility=tuple(feas_lines),
        papers=tuple(PaperForProposal(label, papers[pid].title, papers[pid].year,
                                      *(tuple(analyses[pid].get(k, [])[:6]) for k in ("methods", "datasets",
                                                                                     "metrics", "baselines")))
                     for pid, label in p_label.items()),
        complexity=investigation.complexity,
        constraints=tuple((str(k).replace("_", " "), str(v)) for k, v in (investigation.constraints_snapshot or {})
                          .items() if v not in (None, "", [])),
    )
    claims_by_label = {c_label[c.id]: c for c in claims}
    supported = {label for label, c in claims_by_label.items() if c.status in SUPPORTED_CLAIMS} | \
        {label for pid, label in p_label.items() if pid in verified_papers}
    papers_by_label = {label: papers[pid] for pid, label in p_label.items()}
    return request, claims_by_label, papers_by_label, supported


def check_statements(output, claims_by_label: dict, papers_by_label: dict, supported: set[str]):
    """Applies doc 04's rules. Returns (sections as stored, statements re-tagged, labels dropped)."""
    retagged = dropped = 0
    sections: dict[str, Any] = {"title": name_papers(output.title, papers_by_label),
                                "recommendation_explanation": name_papers(output.recommendation_explanation,
                                                                          papers_by_label)}
    for name in WRITTEN:
        items = []
        for statement in getattr(output, name):
            sources, kept = [], []
            for label in statement.sources:
                if label in claims_by_label:
                    sources.append({"kind": "claim", "id": str(claims_by_label[label].id)})
                    kept.append(label)
                elif label in papers_by_label:
                    sources.append({"kind": "paper", "id": str(papers_by_label[label].id)})
                    kept.append(label)
                else:
                    dropped += 1
            kind = statement.type
            if kind == "evidence" and not any(label in supported for label in kept):
                kind = "to_verify"
                retagged += 1
            items.append({"text": name_papers(statement.text, papers_by_label), "type": kind, "sources": sources,
                          "retagged": kind != statement.type})
        sections[name] = items
    sections["checks"] = {"retagged": retagged, "dropped_labels": dropped}
    return sections, retagged, dropped


async def write_proposal(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    candidate_id: uuid.UUID | None = None,
    settings: RoleLlmSettings | None = None,
    model=None,
) -> ProposalOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage not in (Stage.RESULTS_READY, Stage.WRITING_PROPOSAL):
            raise StaleState(f"Investigation {investigation_id} has no results to write a package from yet.")
        candidate = await choose_candidate(session, investigation_id, candidate_id)
        evaluation = await _evaluation_of(session, candidate)
        if candidate.status == CandidateStatus.FINALIST:
            await set_candidate_status(session, candidate.id, to=CandidateStatus.SELECTED,
                                       expected_from=CandidateStatus.FINALIST)
        if investigation.stage == Stage.RESULTS_READY:
            await advance_investigation(session, investigation_id, to=Stage.WRITING_PROPOSAL,
                                        expected_from=Stage.RESULTS_READY)
        request, claims_by_label, papers_by_label, supported = await build_request(session, investigation, candidate,
                                                                                   evaluation)
        candidate_id, evaluation_id = candidate.id, evaluation.id

    reading = await design_proposal(request, settings=settings, model=model)
    sections, retagged, dropped = check_statements(reading.output, claims_by_label, papers_by_label, supported)

    async with sessions() as session:
        revision = (await session.execute(select(func.coalesce(func.max(ResearchPackage.revision), 0)).where(
            ResearchPackage.candidate_id == candidate_id))).scalar_one() + 1
        package = ResearchPackage(candidate_id=candidate_id, evaluation_id=evaluation_id, revision=revision,
                                  sections=sections, prompt_version=reading.prompt_version,
                                  model=reading.records[-1].model if reading.records else "unknown")
        session.add(package)
        candidate = await session.get(Candidate, candidate_id)
        if candidate.status == CandidateStatus.SELECTED:
            await set_candidate_status(session, candidate_id, to=CandidateStatus.PROPOSAL_READY,
                                       expected_from=CandidateStatus.SELECTED)
        await record_llm_calls(session, reading.records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.RESULTS_READY,
                                    expected_from=Stage.WRITING_PROPOSAL)
        await session.flush()
    return ProposalOutcome(package, candidate, retagged, dropped, reading.records)


# --- the 17 sections, for display ----------------------------------------------------------------------------

def _statement_lines(items: list[dict], papers: dict[uuid.UUID, Paper], claims: dict[uuid.UUID, Claim]) -> list[str]:
    lines = []
    for item in items:
        refs = []
        for source in item.get("sources", []):
            if source["kind"] == "paper" and uuid.UUID(source["id"]) in papers:
                p = papers[uuid.UUID(source["id"])]
                refs.append(f'"{p.title[:60]}" ({p.year or "n.d."})')
            elif source["kind"] == "claim" and uuid.UUID(source["id"]) in claims:
                refs.append(f"claim: {claims[uuid.UUID(source['id'])].text[:60]}")
        note = " (re-tagged by code: no verified source)" if item.get("retagged") else ""
        lines.append(f"[{TYPE_WORDS.get(item['type'], item['type'])}] {item['text']}"
                     + (f" — sources: {'; '.join(refs)}" if refs else "") + note)
    return lines


async def package_sections(session, package: ResearchPackage) -> list[dict]:
    """All 17 sections (doc 02 §5): {"number", "title", "status", "items": [str]}."""
    s = package.sections
    candidate = await session.get(Candidate, package.candidate_id)
    evaluation = await session.get(Evaluation, package.evaluation_id)
    frozen = await stored_verdicts(session, evaluation.id)
    claims = {c.id: c for c in (await session.execute(select(Claim).where(Claim.evaluation_id == evaluation.id)
                                                      .order_by(Claim.created_at))).scalars()}
    cited = {uuid.UUID(src["id"]) for name in WRITTEN for item in s.get(name, []) for src in item.get("sources", [])
             if src["kind"] == "paper"}
    works = (evaluation.novelty_assessment or {}).get("closest_works", [])
    evidence = (await session.execute(
        select(ClaimEvidence, Quote).join(Quote, Quote.id == ClaimEvidence.quote_id)
        .where(ClaimEvidence.claim_id.in_(list(claims)) if claims else false(), Quote.verified.is_(True)))).all()
    ref_ids = list(dict.fromkeys([*cited, *(uuid.UUID(w["paper_id"]) for w in works), *(q.paper_id for _, q in evidence)]))
    papers = {p.id: p for p in (await session.execute(select(Paper).where(Paper.id.in_(ref_ids)))).scalars()} \
        if ref_ids else {}
    challenges = list((await session.execute(select(Challenge).where(
        Challenge.evaluation_id == evaluation.id, Challenge.resolution == "unresolved"))).scalars())
    value = evaluation.value_feasibility or {}

    def verdict(name):
        v = frozen.get(name)
        return (f"{LABELS.get(v.category, v.category)} (rule {v.rule})", list(v.rule_trace)) if v else ("not concluded", [])

    novelty, novelty_trace = verdict("novelty")
    feas, feas_trace = verdict("feasibility")
    recommendation, rec_trace = verdict("recommendation")
    coverage = (evaluation.coverage or {}).get("funnel", {})
    quotes_by_claim: dict[uuid.UUID, list[str]] = {}
    for ce, quote in evidence:
        if quote.paper_id in papers:
            quotes_by_claim.setdefault(ce.claim_id, []).append(
                f'{ce.stance}: "{quote.text[:200]}" ({papers[quote.paper_id].title[:50]})')
    timeline = value.get("timeline") or {}
    feasibility = value.get("feasibility") or {}
    limitations = _statement_lines(s.get("limitations", []), papers, claims) + [
        f"[unresolved Skeptic challenge] {c.argument}" for c in challenges]

    return [
        {"number": 1, "title": "Proposed title", "status": "interpretation", "items": [s.get("title", candidate.title)]},
        {"number": 2, "title": "Research problem and background", "status": "cited evidence",
         "items": _statement_lines(s.get("background", []), papers, claims)},
        {"number": 3, "title": "Research question", "status": "interpretation",
         "items": [candidate.research_question] + ([f"Hypothesis: {candidate.hypothesis}"] if candidate.hypothesis else [])},
        {"number": 4, "title": "Motivation", "status": "cited evidence + interpretation",
         "items": _statement_lines(s.get("motivation", []), papers, claims)},
        {"number": 5, "title": "Potential contribution", "status": f"interpretation, bounded by the novelty verdict ({novelty})",
         "items": _statement_lines(s.get("contribution", []), papers, claims)},
        {"number": 6, "title": "Novelty assessment", "status": "verdict + coverage report",
         "items": [f"Novelty: {novelty}", *novelty_trace,
                   f"Coverage: {coverage.get('unique', '?')} unique papers, {coverage.get('screened', '?')} screened, "
                   f"{coverage.get('relevant', '?')} relevant, {coverage.get('read', '?')} read"]},
        {"number": 7, "title": "Closest related papers and differences", "status": "cited evidence",
         "items": [f"{cite(papers[uuid.UUID(w['paper_id'])])}: did {w['what_they_did']}; the idea differs: "
                   f"{w['how_the_idea_differs']}" for w in works if uuid.UUID(w["paper_id"]) in papers]},
        {"number": 8, "title": "Supporting and contradicting evidence", "status": "cited evidence (verified quotes)",
         "items": [f"[{c.status}] {c.text}" + "".join(f"\n    - {q}" for q in quotes_by_claim.get(c.id, [])[:2])
                   for c in claims.values()]},
        {"number": 9, "title": "Methodology", "status": "interpretation, grounded in cited methods",
         "items": _statement_lines(s.get("methodology", []), papers, claims)},
        {"number": 10, "title": "Hypotheses", "status": "hypothesis",
         "items": _statement_lines(s.get("hypotheses", []), papers, claims)},
        {"number": 11, "title": "Datasets and availability", "status": "checked against the papers read",
         "items": [f"{d['name']}: {d['status'].replace('_', ' ')}" for d in value.get("data_checks", [])]},
        {"number": 12, "title": "Tools and technologies", "status": "interpretation",
         "items": _statement_lines(s.get("tools", []), papers, claims)},
        {"number": 13, "title": "Experiment design", "status": "grounded in cited baselines and metrics",
         "items": _statement_lines(s.get("experiment_design", []), papers, claims)},
        {"number": 14, "title": "Feasibility", "status": "rubric verdict + blocking requirements",
         "items": [f"Feasibility: {feas}", *feas_trace,
                   *([f"Timeline: {timeline.get('total_weeks')} weeks (available: {timeline.get('available_weeks')})"]
                     if timeline else []),
                   *(f"Blocking: {b}" for b in feasibility.get("blocking_requirements", []))]},
        {"number": 15, "title": "Limitations and risks", "status": "mixed, labelled", "items": limitations},
        {"number": 16, "title": "References", "status": "verified metadata, rendered from the database",
         "items": [cite(papers[pid]) for pid in ref_ids if pid in papers]},
        {"number": 17, "title": "Final recommendation", "status": "verdict engine (rules) + explanation",
         "items": [f"Recommendation: {recommendation}", *rec_trace, s.get("recommendation_explanation", "")]},
    ]
