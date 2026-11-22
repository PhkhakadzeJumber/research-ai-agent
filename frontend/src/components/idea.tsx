/**
 * Screen 05 · Idea detail (step 10c): one evaluated idea, with everything behind its verdicts.
 *
 * The four verdicts (each with the rule that decided it), then tabs: Overview, Prior work (how the closest papers
 * overlap each part of the idea), Evidence (claims and their verified quotes), Skeptic log, Feasibility, Coverage.
 * Papers link to the library; their titles come from the database. Verdicts of an evaluation that hasn't concluded
 * are marked preliminary. Used on /ideas/[id] and as the result of a Mode 1 investigation.
 */
"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { FacetGroups } from "@/components/facets";
import { ButtonLink, Card, ErrorNote, MonoLabel, Pill, buttonClass, cx, type Tone } from "@/components/ui";
import { API, type Schemas } from "@/lib/api/client";
import {
  useIdea,
  useIdeaPart,
  type CandidateDetail,
  type ClaimOut,
  type Investigation,
  type Matrix,
  type SkepticLog,
} from "@/lib/api/hooks";
import { DIMENSION_LABELS, KIND_LABELS, verdictTone, words } from "@/lib/format";

type PaperRef = Schemas["PaperRef"];
const TABS = ["Overview", "Prior work", "Evidence", "Skeptic log", "Feasibility", "Coverage"] as const;
type Tab = (typeof TABS)[number];

const OVERLAP: Record<string, [string, Tone, string]> = {
  same: ["same", "red", "="], similar: ["similar", "amber", "~"], different: ["different", "green", "×"],
  not_addressed: ["not addressed", "neutral", "·"], unknown: ["unknown", "neutral", "?"],
};
const RESOLUTION_TONES: Record<string, Tone> = { accepted: "amber", rebutted: "green", unresolved: "red" };
const CLAIM_TONES: Record<string, Tone> = {
  verified: "green", defended: "green", refuted: "red", challenged: "amber", unverified_quote: "amber",
  no_evidence: "neutral", proposed: "neutral",
};
const PACKAGE_STATUSES = ["finalist", "selected", "proposal_ready"];

/** "N4: no verified 'same' on the key facets…" → "No verified 'same' on the key facets…" */
export const traceText = (line: string) => {
  const text = line.replace(/^[A-Z]+\d+[A-Z]*:\s*/, "");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export function IdeaDetail({ ideaId, investigation }: { ideaId: string; investigation?: Investigation }) {
  const idea = useIdea(ideaId);
  const log = useIdeaPart<SkepticLog>(ideaId, "challenges", true);
  const [tab, setTab] = useState<Tab>("Overview");
  if (idea.isPending) return <p className="body-s text-ink-3">Loading the idea…</p>;
  if (idea.error) return <ErrorNote error={idea.error} />;
  const data = idea.data;
  const rounds = log.data?.rounds.length ?? 0;

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-3">
        <MonoLabel>
          <Link href={`/investigations/${data.investigation_id}`} className="hover:text-ink">{investigation?.title ? "Investigation" : "Back to the investigation"}</Link>
          {" / "}<span className="text-ink">{data.origin === "user" ? "Your idea" : "Idea"}</span>
        </MonoLabel>
        <div className="flex flex-wrap gap-2">
          <Pill tone={PACKAGE_STATUSES.includes(data.status) ? "green" : data.status === "rejected" ? "red" : "neutral"}>
            {words(data.status)}{data.version > 1 ? ` · v${data.version}` : ""}
          </Pill>
          {data.evaluation ? (
            <Pill>{data.evaluation.depth === "full" ? "Full" : "Light"} evaluation{rounds ? ` · ${rounds} Skeptic round${rounds === 1 ? "" : "s"}` : ""}</Pill>
          ) : null}
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex max-w-3xl flex-col gap-2.5">
            <h1 className="display-l">{data.title}</h1>
            <p className="text-[17px] leading-relaxed text-ink-2">{data.research_question}</p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <a href={`${API}/investigations/${data.investigation_id}/report`} className={buttonClass("secondary")}>Export report</a>
            {PACKAGE_STATUSES.includes(data.status) ? (
              <ButtonLink href={`/ideas/${data.id}/package`} kind="accent">Research package →</ButtonLink>
            ) : null}
          </div>
        </div>
        {data.preliminary && data.verdicts.length ? (
          <p className="rounded-md bg-amber-soft px-4 py-2.5 body-s text-amber">
            Preliminary: this evaluation hasn&apos;t concluded, so these verdicts may still change.
          </p>
        ) : null}
        {data.change_note ? <p className="body-s text-ink-2"><span className="font-medium">Refined:</span> {data.change_note}</p> : null}
      </header>

      {data.verdicts.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {data.verdicts.map((v) => (
            <Card key={v.dimension} className="flex flex-col gap-2 p-5">
              <div className="flex justify-between">
                <MonoLabel>{DIMENSION_LABELS[v.dimension] ?? v.dimension}</MonoLabel>
                <span className="font-mono text-[11px] text-ink-3" title={v.rules_version}>rule {v.rule}</span>
              </div>
              <span aria-hidden className={cx("h-[3px] w-8", { green: "bg-green", amber: "bg-amber", red: "bg-red", indigo: "bg-indigo" }[verdictTone(v.category) as string] ?? "bg-ink-3")} />
              <span className="font-serif text-[28px] leading-tight">{v.label}</span>
              <span className="body-s line-clamp-4 text-ink-3" title={v.trace[0] ? traceText(v.trace[0]) : undefined}>{v.trace[0] ? traceText(v.trace[0]) : ""}</span>
            </Card>
          ))}
        </div>
      ) : <Card><p className="body-m text-ink-2">No verdicts yet: the evaluation hasn&apos;t reached them.</p></Card>}

      <nav role="tablist" className="flex gap-6 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cx("whitespace-nowrap pb-3 pt-1 text-[15px] font-semibold", tab === t ? "border-b-2 border-ink text-ink" : "text-ink-3 hover:text-ink-2")}>
            {t}
          </button>
        ))}
      </nav>

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="flex min-w-0 flex-col gap-6">
          {tab === "Overview" ? <Overview idea={data} /> : null}
          {tab === "Prior work" ? <PriorWork idea={data} /> : null}
          {tab === "Evidence" ? <Evidence ideaId={data.id} /> : null}
          {tab === "Skeptic log" ? <Skeptic log={log.data} /> : null}
          {tab === "Feasibility" ? <Feasibility idea={data} /> : null}
          {tab === "Coverage" ? <Coverage idea={data} full /> : null}
        </div>
        <aside className="flex flex-col gap-6">
          <Coverage idea={data} />
          <SkepticSummary log={log.data} />
        </aside>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <h2 className="heading-m">{title}</h2>
      {children}
    </Card>
  );
}

export function PaperLink({ paper }: { paper: PaperRef }) {
  return (
    <span>
      <Link href={`/library/${paper.id}`} className="hover:underline">{paper.title}</Link>
      <span className="text-ink-3"> ({paper.year ?? "n.d."}{paper.peer_reviewed ? "" : ", preprint"})</span>
      {paper.link ? <a href={paper.link} target="_blank" rel="noreferrer" className="ml-1 text-indigo" aria-label="Open the paper">↗</a> : null}
    </span>
  );
}

function Overview({ idea }: { idea: CandidateDetail }) {
  const recommendation = idea.verdicts.find((v) => v.dimension === "recommendation");
  return (
    <>
      {idea.decision || recommendation ? (
        <Section title="Why this recommendation">
          {recommendation ? (
            <ul className="flex flex-col gap-1.5 body-m">
              {[...recommendation.trace, ...recommendation.limits.map((l) => `Limit: ${l}`)].map((line) => <li key={line}>{traceText(line)}</li>)}
            </ul>
          ) : null}
          {idea.decision ? (
            <p className="body-s text-ink-2">Triage, rule {idea.decision.rule_number}: {words(idea.decision.action)} ({idea.decision.reason}).</p>
          ) : null}
          {idea.rejection_reason ? <p className="body-s text-red">{idea.rejection_reason}</p> : null}
        </Section>
      ) : null}
      {idea.motivation || idea.hypothesis || idea.potential_contribution ? (
        <Section title="The idea">
          {[["Motivation", idea.motivation], ["Hypothesis", idea.hypothesis], ["Potential contribution", idea.potential_contribution]]
            .filter(([, text]) => text).map(([label, text]) => (
              <div key={label} className="flex flex-col gap-1"><MonoLabel>{label}</MonoLabel><p className="body-m">{text}</p></div>
            ))}
        </Section>
      ) : null}
      <Section title="The idea, in parts">
        <FacetGroups facets={idea.facets} />
      </Section>
      {idea.closest_works.length ? <ClosestWorks idea={idea} /> : null}
    </>
  );
}

function ClosestWorks({ idea }: { idea: CandidateDetail }) {
  return (
    <Section title="Closest work and how the idea differs">
      {idea.closest_works.map((w) => (
        <div key={w.paper.id} className="flex flex-col gap-1.5 rounded-md bg-paper px-4 py-3.5">
          <span className="heading-s text-[15px]"><PaperLink paper={w.paper} /></span>
          <span className="body-s text-ink-2">Did: {w.what_they_did}</span>
          <span className="body-s text-green"><span className="font-semibold">The idea differs:</span> {w.how_the_idea_differs}</span>
          {w.minor_difference_only ? <span className="body-s text-amber">Differs only in a minor part.</span> : null}
        </div>
      ))}
    </Section>
  );
}

function PriorWork({ idea }: { idea: CandidateDetail }) {
  const matrix = useIdeaPart<Matrix>(idea.id, "matrix", true);
  if (matrix.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (matrix.error) return <ErrorNote error={matrix.error} />;
  const { facets, papers, cells } = matrix.data;
  const cell = (paper: string, position: number) => cells.find((c) => c.paper_id === paper && c.facet_position === position);
  return (
    <>
      <Card className="flex flex-col gap-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="heading-m">How the closest papers overlap each part of the idea</h2>
          <span className="flex gap-1.5">{(["same", "similar", "different"] as const).map((k) => <Pill key={k} tone={OVERLAP[k][1]}>{k}</Pill>)}</span>
        </div>
        {papers.length === 0 ? <p className="body-s text-ink-3">No comparison yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse">
              <thead>
                <tr className="label-mono text-ink-3">
                  <th className="py-2.5 text-left font-medium">Paper</th>
                  {facets.map((f) => <th key={f.position} className="w-28 py-2.5 text-center font-medium">{KIND_LABELS[f.kind] ?? f.kind}</th>)}
                </tr>
              </thead>
              <tbody>
                {papers.map((p) => (
                  <tr key={p.id} className="border-t border-line">
                    <td className="py-2.5 pr-3 body-s"><Link href={`/library/${p.id}`} className="hover:underline">{p.title}</Link> <span className="text-ink-3">({p.year ?? "n.d."})</span></td>
                    {facets.map((f) => {
                      const c = cell(p.id, f.position);
                      if (!c) return <td key={f.position} className="text-center text-ink-3">–</td>;
                      const [label, tone, mark] = OVERLAP[c.overlap] ?? OVERLAP.unknown;
                      return (
                        <td key={f.position} className="py-2 text-center" title={c.rationale + (c.quote ? `\n“${c.quote}”` : "")}>
                          <Pill tone={tone}>{mark} {label}{c.quote ? " ✓" : ""}</Pill>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="body-s text-ink-3">✓ = backed by a verified quote (hover a cell to read it). Coverage is limited to the papers searched.</p>
      </Card>
      {idea.closest_works.length ? <ClosestWorks idea={idea} /> : null}
    </>
  );
}

function Evidence({ ideaId }: { ideaId: string }) {
  const claims = useIdeaPart<ClaimOut[]>(ideaId, "claims", true);
  if (claims.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (claims.error) return <ErrorNote error={claims.error} />;
  const byKind = Object.entries(claims.data.reduce<Record<string, ClaimOut[]>>((acc, c) => ((acc[c.kind] ??= []).push(c), acc), {}));
  if (!byKind.length) return <Card><p className="body-m text-ink-2">No claims recorded.</p></Card>;
  return (
    <>
      {byKind.map(([kind, list]) => (
        <Section key={kind} title={words(kind).replace(/^./, (s) => s.toUpperCase())}>
          {list.map((c) => (
            <div key={c.id} className="flex flex-col gap-2 border-t border-line pt-3">
              <div className="flex flex-wrap items-center gap-2">
                <Pill tone={CLAIM_TONES[c.status] ?? "neutral"}>{words(c.status)}</Pill>
                <span className="label-mono">{words(c.statement_type)}{c.inferred ? " · inferred" : ""}</span>
              </div>
              <p className="body-m">{c.text}</p>
              {c.evidence.map((q) => (
                <div key={q.quote_id} className="flex flex-col gap-1 rounded-md bg-paper px-4 py-2.5">
                  <span className="font-serif text-[17px] italic text-ink-2">“{q.text}”</span>
                  <span className="body-s text-ink-3">
                    {q.stance} · {q.verified ? "verified word for word" : "not verified"} · from the {words(q.source_level)} of <PaperLink paper={q.paper} />
                  </span>
                </div>
              ))}
            </div>
          ))}
        </Section>
      ))}
    </>
  );
}

function Skeptic({ log }: { log: SkepticLog | undefined }) {
  if (!log) return <p className="body-s text-ink-3">Loading…</p>;
  if (!log.rounds.length) return <Card><p className="body-m text-ink-2">No Skeptic rounds in this evaluation.</p></Card>;
  return (
    <Section title="Skeptic log">
      <p className="body-s text-ink-2">
        {log.rounds.map((r) => `Round ${r.round}: ${r.new_relevant} new relevant papers, ${r.challenges} challenge${r.challenges === 1 ? "" : "s"}${r.saturated ? " (nothing new left)" : ""}`).join(" · ")}
      </p>
      {log.challenges.map((c) => (
        <div key={c.id} className="flex flex-col gap-2 border-t border-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="label-mono">R{c.round}</span>
            <Pill tone={RESOLUTION_TONES[c.resolution] ?? "neutral"}>{c.resolution}</Pill>
            <span className="label-mono">{words(c.type)}</span>
          </div>
          <p className="body-m">{c.argument}</p>
          {c.new_papers.length ? (
            <ul className="flex flex-col gap-1 body-s text-ink-2">{c.new_papers.map((p) => <li key={p.id}><PaperLink paper={p} /></li>)}</ul>
          ) : null}
        </div>
      ))}
    </Section>
  );
}

function Feasibility({ idea }: { idea: CandidateDetail }) {
  const f = idea.feasibility;
  if (!f) return <Card><p className="body-m text-ink-2">Feasibility wasn&apos;t assessed in this evaluation.</p></Card>;
  const lists: [string, string[]][] = [["Skill gaps", f.skill_gaps], ["Risks", f.risks], ["Ethics", f.ethics], ["Simplifications", f.simplifications], ["Who benefits", f.beneficiaries]];
  return (
    <Section title="Feasibility for you">
      {f.total_weeks ? (
        <p className="body-m">Timeline: <span className="font-semibold">{f.total_weeks} weeks</span>{f.available_weeks ? ` of ${f.available_weeks} available` : ""}.</p>
      ) : null}
      {f.compute_needed ? <p className="body-m">Compute: {f.compute_needed}</p> : null}
      {f.data_checks.length ? (
        <div className="flex flex-col gap-1.5">
          <MonoLabel>Data</MonoLabel>
          {f.data_checks.map((d) => (
            <span key={d.name} className="flex items-center gap-2 body-s">
              <Pill tone={d.status.includes("confirmed") ? "green" : d.status.includes("not") ? "red" : "amber"}>{words(d.status)}</Pill>{d.name}
            </span>
          ))}
        </div>
      ) : null}
      {lists.filter(([, items]) => items.length).map(([label, items]) => (
        <div key={label} className="flex flex-col gap-1">
          <MonoLabel>{label}</MonoLabel>
          <ul className="flex list-disc flex-col gap-1 pl-4 body-s">{items.map((i) => <li key={i}>{i}</li>)}</ul>
        </div>
      ))}
    </Section>
  );
}

function Coverage({ idea, full }: { idea: CandidateDetail; full?: boolean }) {
  const c = idea.coverage;
  if (!c) return full ? <Card><p className="body-m text-ink-2">No coverage yet.</p></Card> : null;
  return (
    <Card className="flex flex-col gap-3 p-6">
      <MonoLabel>Coverage{c.frozen ? "" : " (so far)"}</MonoLabel>
      {[["Unique papers", c.unique], ["Screened", c.screened], ["Relevant", c.relevant], ["Read", c.read]].map(([k, v]) => (
        <div key={k} className="flex justify-between body-s"><span className="text-ink-2">{k}</span><span className="heading-s">{v}</span></div>
      ))}
      {full ? (
        <p className="body-s text-ink-2">
          Sources: {c.sources.join(", ") || "none"} · years {c.years_from ?? "?"}–{c.years_to ?? "?"} · {c.read_abstract_only} of {c.read} read from the abstract only
        </p>
      ) : null}
      {c.blind_spots.length || c.budgets_hit.length ? (
        <>
          <MonoLabel>Blind spots</MonoLabel>
          <ul className="flex flex-col gap-1 body-s text-ink-3">
            {[...c.blind_spots, ...c.budgets_hit.map((b) => `budget: ${b}`)].slice(0, full ? undefined : 4).map((s) => <li key={s}>· {s}</li>)}
          </ul>
        </>
      ) : null}
    </Card>
  );
}

function SkepticSummary({ log }: { log: SkepticLog | undefined }) {
  if (!log?.challenges.length) return null;
  return (
    <Card dark className="flex flex-col gap-3 p-6">
      <MonoLabel className="text-accent">Skeptic log</MonoLabel>
      {log.challenges.slice(0, 4).map((c) => (
        <div key={c.id} className="flex flex-col gap-1.5">
          <span className="flex items-center gap-2"><span className="font-mono text-[11px] text-ink-3">R{c.round}</span><Pill tone={RESOLUTION_TONES[c.resolution] ?? "neutral"}>{c.resolution}</Pill></span>
          <span className="body-s line-clamp-3 text-paper">{c.argument}</span>
        </div>
      ))}
      {log.challenges.length > 4 ? <span className="body-s text-ink-3">+ {log.challenges.length - 4} more in the Skeptic log tab</span> : null}
    </Card>
  );
}
