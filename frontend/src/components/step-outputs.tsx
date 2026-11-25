/**
 * The main block of a step page (Figma pages 3 and 4): what the step produced. The searches, the screening, the
 * papers read, the open problems and the idea lists come with the step page itself; the rest reuses what other
 * pages already show in full (the idea's prior-work table, Skeptic log, feasibility and verdicts; the landscape;
 * the ranked ideas; the research package), so a step and the idea page always agree.
 */
"use client";

import Link from "next/link";

import { FacetGroups } from "@/components/facets";
import { Feasibility, PaperLink, PriorWork, Skeptic, VerdictCards } from "@/components/idea";
import { IdeaCard } from "@/components/results";
import { ButtonLink, Card, ErrorNote, MonoLabel, Pill, cx, type Tone } from "@/components/ui";
import { useIdea, useIdeaPart, useLandscape, useResults, type Investigation, type SkepticLog } from "@/lib/api/hooks";
import { words } from "@/lib/format";
import { stepHref, type StepPage } from "@/lib/steps";

type Output = StepPage["output"];
type Query = Extract<Output, { kind: "search" }>["queries"][number];
const SOURCE_NAMES: Record<string, string> = { openalex: "OpenAlex", arxiv: "arXiv" };

/** One row per phrasing, with what each source returned for it (the API lists one search per source). */
export function searchTable(queries: Query[]): { sources: string[]; rows: { text: string; bySource: Record<string, Query> }[] } {
  const sources = [...new Set(queries.map((q) => q.source))].sort();
  const rows: { text: string; bySource: Record<string, Query> }[] = [];
  for (const q of queries) {
    let row = rows.find((r) => r.text === q.text);
    if (!row) rows.push((row = { text: q.text, bySource: {} }));
    row.bySource[q.source] = q;
  }
  return { sources, rows };
}

function Block({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <Card className="flex min-w-0 flex-col gap-3 p-6">
      <div className="flex flex-col gap-1">
        <h2 className="heading-m">{title}</h2>
        {sub ? <p className="body-s text-ink-3">{sub}</p> : null}
      </div>
      {children}
    </Card>
  );
}

const DECISION_TONES: Record<string, Tone> = {
  light_evaluation: "spark", reserve: "neutral", screened_out: "red", pursue: "green", refine: "amber", reconsider: "red",
};

export function StepOutputView({ page, investigation }: { page: StepPage; investigation: Investigation }) {
  const output: Output = page.output;
  if (page.did === "Not reached yet.") return null;
  switch (output.kind) {
    case "search": {
      const { sources, rows } = searchTable(output.queries);
      return (
        <Block title="The searches" sub="Each phrasing, and for each source how many papers it returned (found) and how many no earlier search had found (new).">
          {rows.length === 0 ? <p className="body-s text-ink-3">No searches recorded.</p> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] border-collapse text-left">
                <thead>
                  <tr className="label-mono text-ink-3">
                    <th className="py-2 font-medium">Phrasing</th>
                    {sources.map((s) => <th key={s} className="py-2 pl-4 text-right font-medium">{SOURCE_NAMES[s] ?? s} · found / new</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.text} className="border-t border-line">
                      <td className="py-2.5 pr-3 body-m">{row.text}</td>
                      {sources.map((s) => {
                        const q = row.bySource[s];
                        return (
                          <td key={s} className="py-2.5 pl-4 text-right body-s">
                            {q ? <>{q.found} <span className="text-ink-3">/</span> <span className={q.new === 0 ? "text-ink-3" : "font-semibold"}>{q.new}</span></> : <span className="text-ink-3">–</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Block>
      );
    }
    case "screening": {
      const total = Math.max(1, output.screened);
      return (
        <Block title="Kept and dropped" sub="Every paper scored from 0 (unrelated) to 3 (directly relevant), with the model's reason.">
          <div className="flex flex-col gap-1.5">
            {(["3", "2", "1", "0"] as const).map((score) => (
              <div key={score} className="flex items-center gap-3 body-s">
                <span className="w-24 text-ink-2">{{ "3": "Directly", "2": "Relevant", "1": "Loosely", "0": "Unrelated" }[score]}</span>
                <span className="h-2 flex-1 overflow-hidden rounded-pill bg-surface-muted">
                  <span className={cx("block h-full rounded-pill", score >= "2" ? "bg-green" : "bg-ink-3/40")} style={{ width: `${((output.scores[score] ?? 0) / total) * 100}%` }} />
                </span>
                <span className="w-12 text-right font-mono text-[12px]">{output.scores[score] ?? 0}</span>
              </div>
            ))}
          </div>
          <MonoLabel className="mt-2 text-green">Kept · {output.relevant} relevant, {output.selected} read closely</MonoLabel>
          {output.kept.map((p) => (
            <div key={p.paper.id} className="flex flex-col gap-1 border-t border-line pt-2.5">
              <span className="flex flex-wrap items-center gap-2 body-m"><PaperLink paper={p.paper} />{p.read ? <Pill tone="indigo">read</Pill> : null}<Pill tone="green">{`score ${p.score}`}</Pill></span>
              {p.reason ? <span className="body-s text-ink-2">{p.reason}</span> : null}
            </div>
          ))}
          <MonoLabel className="mt-2">Dropped · {output.screened - output.relevant} (examples)</MonoLabel>
          {output.dropped.slice(0, 12).map((p) => (
            <div key={p.paper.id} className="flex flex-col gap-1 border-t border-line pt-2.5">
              <span className="body-s"><PaperLink paper={p.paper} /> <span className="text-ink-3">· score {p.score}</span></span>
              {p.reason ? <span className="body-s text-ink-3">{p.reason}</span> : null}
            </div>
          ))}
        </Block>
      );
    }
    case "reading":
      return (
        <Block title="Papers read closely" sub={`${output.read} papers · ${output.findings} findings · ${output.quotes_verified} of ${output.quotes} quotes verified word for word. The most informative first.`}>
          {output.papers.map((p) => (
            <div key={p.paper.id} className="flex flex-col gap-2 border-t border-line pt-3">
              <span className="flex flex-wrap items-center gap-2 heading-s text-[15px]"><PaperLink paper={p.paper} /><Pill tone={p.source_level === "full_text" ? "indigo" : "neutral"}>{words(p.source_level)}</Pill></span>
              {p.findings.slice(0, 3).map((f, n) => (
                <div key={n} className="flex flex-col gap-1 rounded-md bg-paper px-4 py-2.5">
                  <span className="body-m">{f.text}</span>
                  {f.quote ? (
                    <span className="flex flex-wrap items-center gap-2 body-s text-ink-2">
                      <span className="italic">“{f.quote}”</span>
                      <Pill tone={f.verified ? "green" : "amber"}>{f.verified ? "quote verified" : "not found in paper"}</Pill>
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          ))}
        </Block>
      );
    case "problems":
      return (
        <Block title="Open problems" sub="Problems several papers point to, each with one of their quotes.">
          {output.problems.length === 0 ? <p className="body-s text-ink-3">No shared open problems found.</p> : null}
          {output.problems.map((p) => (
            <div key={p.label} className="flex flex-col gap-1.5 rounded-md bg-paper px-4 py-3">
              <span className="flex flex-wrap items-center justify-between gap-2"><span className="heading-s text-[15px]">{p.label}</span><Pill tone="violet">{`${p.papers} papers`}</Pill></span>
              {p.quote ? <span className="flex flex-wrap items-center gap-2 body-s text-ink-2"><span className="italic">“{p.quote}”</span>{p.quote_verified ? <Pill tone="green">quote verified</Pill> : null}</span> : null}
            </div>
          ))}
        </Block>
      );
    case "ideas":
    case "prescreen":
    case "light":
    case "full":
      return <IdeaRows output={output} investigationId={investigation.id} />;
    case "ref":
      return <RefView view={output.view} candidateId={output.candidate_id ?? null} investigation={investigation} />;
  }
}

function IdeaRows({ output, investigationId }: { output: Extract<Output, { kind: "ideas" | "prescreen" | "light" | "full" }>; investigationId: string }) {
  const title = { ideas: "The ideas", prescreen: "Kept and dropped", light: "Light results", full: "The finalists" }[output.kind];
  const sub = {
    ideas: "Each idea and how the Scout made it.", prescreen: "Each idea, the decision and why.",
    light: "Each light evaluation and its verdict.", full: "Open a finalist to see each step of its evaluation.",
  }[output.kind];
  return (
    <Block title={title} sub={sub}>
      {output.ideas.length === 0 ? <p className="body-s text-ink-3">None yet.</p> : null}
      {output.ideas.map((idea) => (
        <div key={idea.candidate_id} className="flex flex-wrap items-center gap-3 border-t border-line pt-3">
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <Link href={`/ideas/${idea.candidate_id}`} className="heading-s text-[15px] hover:underline">{idea.title}</Link>
            <span className="body-s text-ink-2">{idea.detail ?? idea.research_question}</span>
          </span>
          {idea.decision ? <Pill tone={DECISION_TONES[idea.decision] ?? "neutral"}>{words(idea.decision)}</Pill> : <Pill>{words(idea.status)}</Pill>}
          {output.kind === "full" ? <ButtonLink href={stepHref(investigationId, "search", idea.candidate_id)} kind="secondary">Open its steps →</ButtonLink> : null}
        </div>
      ))}
    </Block>
  );
}

function RefView({ view, candidateId, investigation }: { view: string; candidateId: string | null; investigation: Investigation }) {
  if (view === "landscape") return <LandscapeBlock investigationId={investigation.id} />;
  if (view === "ranking") return <RankingBlock investigationId={investigation.id} />;
  if (!candidateId) return null;
  if (view === "package") return <PackageBlock candidateId={candidateId} />;
  return <IdeaPart view={view} candidateId={candidateId} />;
}

function IdeaPart({ view, candidateId }: { view: string; candidateId: string }) {
  const idea = useIdea(candidateId);
  const log = useIdeaPart<SkepticLog>(candidateId, "challenges", view === "challenges");
  if (idea.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (idea.error) return <ErrorNote error={idea.error} />;
  if (view === "clarification") {
    return (
      <Block title={idea.data.title} sub={idea.data.research_question}>
        <FacetGroups facets={idea.data.facets} />
      </Block>
    );
  }
  if (view === "matrix") return <PriorWork idea={idea.data} />;
  if (view === "challenges") return <Skeptic log={log.data} />;
  if (view === "feasibility") return <Feasibility idea={idea.data} />;
  return (
    <div className="flex flex-col gap-4">
      <VerdictCards idea={idea.data} />
      <div className="flex flex-wrap gap-2.5">
        <ButtonLink href={`/ideas/${candidateId}`} kind="secondary">See the idea in full</ButtonLink>
        {["finalist", "selected", "proposal_ready"].includes(idea.data.status) ? (
          <ButtonLink href={`/ideas/${candidateId}/package`} kind="accent">Research package →</ButtonLink>
        ) : null}
      </div>
    </div>
  );
}

function PackageBlock({ candidateId }: { candidateId: string }) {
  return (
    <Block title="The research package" sub="17 sections; every statement says whether it is evidence, an interpretation, a hypothesis or something to verify.">
      <div><ButtonLink href={`/ideas/${candidateId}/package`} kind="primary">Open the research package →</ButtonLink></div>
    </Block>
  );
}

function LandscapeBlock({ investigationId }: { investigationId: string }) {
  const landscape = useLandscape(investigationId, true);
  if (landscape.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (landscape.error) return <ErrorNote error={landscape.error} />;
  const l = landscape.data;
  return (
    <Block title="The map" sub={`${l.papers_found} papers found · ${l.papers_read} read closely · ${l.subtopics.length} subtopics`}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse text-left">
          <thead><tr className="label-mono text-ink-3"><th className="py-2 font-medium">Subtopic</th><th className="py-2 text-right font-medium">Found</th><th className="py-2 text-right font-medium">Read</th></tr></thead>
          <tbody>
            {l.subtopics.map((s) => (
              <tr key={s.name} className="border-t border-line">
                <td className="py-2.5 pr-3"><span className="body-m">{s.name}</span><span className="block body-s text-ink-3">{s.why_it_fits_you}</span></td>
                <td className="py-2.5 text-right body-s">{s.papers_found}</td>
                <td className="py-2.5 text-right body-s">{s.papers_read}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Block>
  );
}

function RankingBlock({ investigationId }: { investigationId: string }) {
  const results = useResults(investigationId);
  if (results.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (results.error) return <ErrorNote error={results.error} />;
  const r = results.data;
  return (
    <div className="flex flex-col gap-4">
      {r.finalists.length === 0 ? <Card><p className="body-m text-ink-2">No idea completed a full evaluation as a finalist.</p></Card> : null}
      {r.finalists.map((idea) => <IdeaCard key={idea.candidate.id} idea={idea} hero={idea.rank === 1} />)}
      {r.others.length ? (
        <Block title="Stopped earlier" sub="Each with the reason.">
          {r.others.map((o) => (
            <div key={o.candidate.id} className="flex flex-col gap-0.5 border-t border-line pt-2.5">
              <Link href={`/ideas/${o.candidate.id}`} className="heading-s text-[15px] hover:underline">{o.candidate.title}</Link>
              <span className="body-s text-ink-3">{o.why}</span>
            </div>
          ))}
        </Block>
      ) : null}
    </div>
  );
}
