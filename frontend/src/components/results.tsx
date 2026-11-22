/**
 * The results of an investigation (step 10c).
 *
 * Mode 2 (screen 04): the ideas ranked by rules (Pursue before Refine, then novelty, feasibility, significance,
 * evidence), each with its verdicts and why it is above the next; the landscape strip; the open problems; the ideas
 * set aside, each with its reason.
 * Mode 1: the user's own idea, in full (the idea detail, screen 05), then any refined versions and alternatives.
 */
"use client";

import Link from "next/link";

import { IdeaDetail } from "@/components/idea";
import { ButtonLink, Card, ErrorNote, MonoLabel, Pill, buttonClass, cx } from "@/components/ui";
import { API } from "@/lib/api/client";
import { useLandscape, useResults, type Investigation, type RankedIdea, type Results } from "@/lib/api/hooks";
import { verdictTone, words } from "@/lib/format";

export function ResultsView({ investigation }: { investigation: Investigation }) {
  const results = useResults(investigation.id);
  if (results.isPending) return <p className="body-s text-ink-3">Loading the results…</p>;
  if (results.error) return <ErrorNote error={results.error} />;
  return investigation.mode === "discover"
    ? <DiscoverResults investigation={investigation} results={results.data} />
    : <EvaluateResults investigation={investigation} results={results.data} />;
}

export function ownIdea(results: Results) {
  const all = [...results.finalists.map((r) => r.candidate), ...results.advanced.map((r) => r.candidate), ...results.others.map((o) => o.candidate)];
  return all.find((c) => c.origin === "user" && c.version === 1) ?? null;
}

function EvaluateResults({ investigation, results }: { investigation: Investigation; results: Results }) {
  const own = ownIdea(results);
  const more = [...results.finalists, ...results.advanced].filter((r) => r.candidate.id !== own?.id);
  const setAside = results.others.filter((o) => o.candidate.id !== own?.id);
  if (!own) return <ErrorNote error={new Error("This investigation has no confirmed idea.")} />;
  return (
    <div className="flex flex-col gap-10">
      <IdeaDetail ideaId={own.id} investigation={investigation} />
      {more.length || setAside.length ? (
        <section className="flex flex-col gap-4">
          <h2 className="display-m">Refined versions and <em>alternatives</em></h2>
          {more.map((r) => <IdeaCard key={r.candidate.id} idea={r} />)}
          {setAside.length ? <SetAside items={setAside} /> : null}
        </section>
      ) : null}
    </div>
  );
}

function DiscoverResults({ investigation, results }: { investigation: Investigation; results: Results }) {
  const landscape = useLandscape(investigation.id, true);
  const field = (investigation.input as { field?: string }).field ?? "your field";
  const ideas = results.finalists.length + results.advanced.length + results.others.length;
  const strip = [
    ["Papers mapped", landscape.data?.papers_found, landscape.data ? `${landscape.data.subtopics.length} subtopics` : ""],
    ["Read closely", landscape.data?.papers_read, "in the landscape"],
    ["Open problems", landscape.data?.open_problems.length, "3+ papers each"],
    ["Ideas proposed", ideas, `${results.finalists.length} finalist${results.finalists.length === 1 ? "" : "s"}`],
    ["Credits", investigation.credits.free ? "Free" : investigation.credits.used, investigation.credits.free ? "free plan" : "used"],
  ] as const;

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2.5">
        <MonoLabel>Investigations / Find me an idea · {field} / <span className="text-ink">Results</span></MonoLabel>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2.5">
            <h1 className="display-l">Ideas worth your <em>time, ranked.</em></h1>
            <p className="body-m text-ink-2">
              Ordered by rules, not a score: Pursue before Refine, then novelty, feasibility, significance and evidence.
            </p>
          </div>
          <a href={`${API}/investigations/${investigation.id}/report`} className={buttonClass("secondary")}>Full report</a>
        </div>
      </header>

      <Card className="grid grid-cols-2 gap-0 p-0 sm:grid-cols-5">
        {strip.map(([label, value, sub], n) => (
          <div key={label} className={cx("flex flex-col gap-1 px-6 py-5", n > 0 && "sm:border-l sm:border-line")}>
            <MonoLabel>{label}</MonoLabel>
            <span className="display-m">{value ?? "–"}</span>
            <span className="body-s text-ink-3">{sub}</span>
          </div>
        ))}
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="flex flex-col gap-4">
          {results.finalists.length === 0 ? (
            <Card><p className="body-m text-ink-2">No idea completed a full evaluation as a finalist. The ideas below passed screening or were set aside, each with the reason.</p></Card>
          ) : null}
          {results.finalists.map((r) => <IdeaCard key={r.candidate.id} idea={r} hero={r.rank === 1} />)}
          {results.advanced.length ? (
            <>
              <h2 className="heading-m mt-4">Passed screening, not fully evaluated</h2>
              {results.advanced.map((r) => <IdeaCard key={r.candidate.id} idea={r} />)}
            </>
          ) : null}
        </div>
        <aside className="flex flex-col gap-6">
          {landscape.data?.open_problems.length ? (
            <Card className="flex flex-col gap-3 p-6">
              <MonoLabel>Open problems in the literature</MonoLabel>
              {landscape.data.open_problems.map((p) => (
                <div key={p.id} className="flex items-start gap-3">
                  <span className="rounded-sm bg-accent-soft px-2 py-0.5 font-mono text-[11px]">{p.papers}</span>
                  <span className="body-s">{p.label}</span>
                </div>
              ))}
              <p className="body-s text-ink-3">Each one: 3+ papers from independent author groups, verified quotes only.</p>
            </Card>
          ) : null}
          {results.others.length ? <SetAside items={results.others} /> : null}
        </aside>
      </div>
    </div>
  );
}

const STATUS_LINES: Record<string, string> = {
  finalist: "Finalist", selected: "Finalist · chosen", proposal_ready: "Finalist · research package ready",
  advanced: "Passed screening · light evaluation",
};

export function IdeaCard({ idea, hero }: { idea: RankedIdea; hero?: boolean }) {
  const v = idea.verdicts;
  const verdicts = [v.recommendation, v.novelty, v.feasibility, v.significance].filter(Boolean);
  return (
    <Card className={cx("flex gap-6", hero && "border-[1.5px] border-ink")}>
      <span className={cx("font-serif text-[80px] italic leading-[0.9]", hero ? "text-accent" : "text-ink-3")}>{idea.rank}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <MonoLabel className={hero ? "text-green" : undefined}>{STATUS_LINES[idea.candidate.status] ?? words(idea.candidate.status)}</MonoLabel>
        <h3 className={cx("font-serif leading-tight", hero ? "text-[34px]" : "text-[28px]")}>{idea.candidate.title}</h3>
        <p className="body-m text-ink-2">{idea.candidate.research_question}</p>
        <div className="flex flex-wrap gap-2">
          {verdicts.map((x) => <Pill key={x!.category} tone={verdictTone(x!.category)}>{x!.label}</Pill>)}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="body-s text-ink-3">
            {idea.supporting_quotes} verified supporting quote{idea.supporting_quotes === 1 ? "" : "s"} · {idea.depth} evaluation
            {idea.ahead_because ? ` · above the next on ${idea.ahead_because}` : ""}
          </span>
          <span className="flex gap-2">
            {idea.has_package || idea.candidate.status === "finalist" ? (
              <ButtonLink href={`/ideas/${idea.candidate.id}/package`} kind="accent">Research package</ButtonLink>
            ) : null}
            <ButtonLink href={`/ideas/${idea.candidate.id}`} kind={hero ? "primary" : "secondary"}>Open →</ButtonLink>
          </span>
        </div>
      </div>
    </Card>
  );
}

function SetAside({ items }: { items: Results["others"] }) {
  return (
    <Card tone="muted" className="flex flex-col gap-3.5 p-6">
      <MonoLabel>Set aside · {items.length}</MonoLabel>
      {items.map((o) => (
        <div key={o.candidate.id} className="flex flex-col gap-1.5">
          <Link href={`/ideas/${o.candidate.id}`} className="heading-s text-sm hover:underline">{o.candidate.title}</Link>
          <span className="body-s text-ink-2">
            <span className="font-medium">{words(o.candidate.status)}:</span> {o.why}
          </span>
        </div>
      ))}
    </Card>
  );
}
