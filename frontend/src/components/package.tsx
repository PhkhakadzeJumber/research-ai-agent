/**
 * Screen 06 · Research package (step 10c): the 17 sections A7 and code wrote for a finalist (doc 02 §5).
 *
 * The written sections show each statement with its type (evidence / interpretation / hypothesis / to verify) and
 * its sources (papers link to the library; claims show their text); the other sections are rendered by code from the
 * evaluation. Export as Markdown. With no package yet, the user can have it written (one paid call, about
 * $0.35-0.50): the page follows the writing until the package is there.
 */
"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card, ErrorNote, MonoLabel, Pill, buttonClass, cx } from "@/components/ui";
import { API } from "@/lib/api/client";
import { useIdea, useInvestigation, usePackage, useWritePackage, type ResearchPackage } from "@/lib/api/hooks";
import { STATEMENT_TONES } from "@/lib/format";

export const shortTitle = (title: string, max = 48) =>
  title.length > max ? `${title.slice(0, max).replace(/[\s:,;-]+\S*$/, "")}…` : title;

const PACKAGE_STATUSES = ["finalist", "selected", "proposal_ready"];

export function PackageScreen({ ideaId }: { ideaId: string }) {
  const idea = useIdea(ideaId);
  const investigation = useInvestigation(idea.data?.investigation_id ?? "");
  const writing = investigation.data?.run === "running";
  const pkg = usePackage(ideaId, writing);
  const write = useWritePackage(ideaId);

  if (idea.isPending || pkg.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (idea.error || pkg.error) return <ErrorNote error={idea.error ?? pkg.error} />;
  const crumb = (
    <MonoLabel>
      <Link href={`/investigations/${idea.data.investigation_id}`} className="hover:text-ink">Results</Link> /{" "}
      <Link href={`/ideas/${ideaId}`} className="hover:text-ink">{shortTitle(idea.data.title)}</Link> / <span className="text-ink">Research package</span>
    </MonoLabel>
  );
  if (pkg.data) return <PackageDocument pkg={pkg.data} crumb={crumb} />;

  const canWrite = PACKAGE_STATUSES.includes(idea.data.status);
  return (
    <div className="flex flex-col gap-6">
      {crumb}
      <Card className="flex max-w-2xl flex-col gap-4">
        <h1 className="display-m">The research package</h1>
        <p className="body-m text-ink-2">
          A 17-section plan for <span className="font-medium text-ink">{idea.data.title}</span>: background, method,
          hypotheses, datasets, experiment design, risks, references. Every statement says whether it is evidence
          (with its source), interpretation, a hypothesis, or something to verify.
        </p>
        {writing || write.isSuccess ? (
          <p role="status" className="rounded-md bg-accent-soft px-4 py-3 body-s">
            Writing it now (the Proposal Designer, about 2 minutes). This page shows it when it&apos;s ready.
          </p>
        ) : canWrite ? (
          <>
            <p className="body-s text-ink-3">One call to the Proposal Designer: about $0.35–0.50, counted in this investigation&apos;s budget.</p>
            <ErrorNote error={write.error} />
            <div><Button kind="accent" disabled={write.isPending} onClick={() => write.mutate()}>Write the research package →</Button></div>
          </>
        ) : (
          <p className="body-s text-ink-2">A research package is written for a finalist; this idea is {idea.data.status.replaceAll("_", " ")}.</p>
        )}
      </Card>
    </div>
  );
}

function PackageDocument({ pkg, crumb }: { pkg: ResearchPackage; crumb: React.ReactNode }) {
  const [active, setActive] = useState(pkg.sections[0]?.number ?? 1);
  const title = pkg.sections[0]?.items[0];
  return (
    <div className="flex flex-col gap-7">
      {crumb}
      <div className="grid gap-10 lg:grid-cols-[260px_1fr]">
        <nav aria-label="Sections" className="flex flex-col gap-1 self-start lg:sticky lg:top-6">
          <MonoLabel className="mb-1">{pkg.sections.length} sections</MonoLabel>
          {pkg.sections.map((s) => (
            <a key={s.number} href={`#section-${s.number}`} onClick={() => setActive(s.number)}
              className={cx("flex items-center gap-2.5 rounded-sm px-3 py-1.5 body-s", active === s.number ? "border border-line bg-surface font-semibold text-ink" : "text-ink-2 hover:text-ink")}>
              <span className={cx("font-mono text-[11px]", active === s.number ? "text-amber" : "text-ink-3")}>{String(s.number).padStart(2, "0")}</span>
              {s.title}
            </a>
          ))}
        </nav>

        <Card className="flex min-w-0 flex-col gap-7 p-6 sm:p-12">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <MonoLabel>Research package · revision {pkg.revision} · grounded in one frozen evaluation</MonoLabel>
            <a href={`${API}/candidates/${pkg.candidate_id}/proposal/export?format=md`} className={buttonClass("secondary")}>Markdown</a>
          </div>
          {title ? <h1 className="display-l text-[42px] leading-tight">{title}</h1> : null}
          <div className="flex flex-wrap items-center gap-2">
            <span className="body-s text-ink-3">Every statement says what it is:</span>
            {Object.entries(STATEMENT_TONES).map(([type, tone]) => <Pill key={type} tone={tone}>{type.replace("_", " ")}</Pill>)}
          </div>
          {pkg.checks.retagged ? (
            <p className="body-s text-amber">
              {pkg.checks.retagged} statement{pkg.checks.retagged === 1 ? " was" : "s were"} marked “to verify” by code: no verified source backs them.
            </p>
          ) : null}
          <hr className="border-line" />

          {pkg.sections.slice(1).map((section) => (
            <section key={section.number} id={`section-${section.number}`} className="flex scroll-mt-6 flex-col gap-3.5">
              <div className="flex flex-wrap items-baseline gap-3">
                <span className="font-serif text-[32px] italic leading-none text-accent">{section.number}</span>
                <h2 className="heading-m">{section.title}</h2>
                <span className="font-mono text-[11px] text-ink-3">{section.status}</span>
              </div>
              {section.statements ? (
                section.statements.length ? section.statements.map((st, n) => (
                  <div key={n} className="flex flex-col gap-2 rounded-md bg-paper px-4 py-3 sm:flex-row sm:gap-3.5">
                    <span className="w-[118px] shrink-0"><Pill tone={STATEMENT_TONES[st.type] ?? "neutral"}>{st.label}</Pill></span>
                    <div className="flex flex-col gap-2">
                      <p className="body-m">{st.text}</p>
                      {st.sources.length ? (
                        <div className="flex flex-wrap gap-1.5">
                          {st.sources.map((src) => src.kind === "paper" ? (
                            <Link key={src.id} href={`/library/${src.id}`} className="rounded-sm border border-line bg-surface px-2 py-0.5 label-s text-indigo">↗ {src.label}</Link>
                          ) : (
                            <span key={src.id} className="rounded-sm border border-line bg-surface px-2 py-0.5 label-s text-ink-2" title={src.label}>claim: {src.label.slice(0, 60)}{src.label.length > 60 ? "…" : ""}</span>
                          ))}
                        </div>
                      ) : null}
                      {st.retagged ? <span className="body-s text-amber">Marked “to verify” by code: no verified source.</span> : null}
                    </div>
                  </div>
                )) : <p className="body-s text-ink-3">Nothing here.</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {section.items.map((item, n) => <li key={n} className="body-m whitespace-pre-line">{item}</li>)}
                  {section.items.length === 0 ? <li className="body-s text-ink-3">Nothing here.</li> : null}
                </ul>
              )}
            </section>
          ))}
          <p className="body-s text-ink-3">
            Statements marked “to verify” are needed by the plan but not backed by the evidence Eureka found: check them
            before relying on them. Novelty is limited to the sources and papers searched.
          </p>
        </Card>
      </div>
    </div>
  );
}
