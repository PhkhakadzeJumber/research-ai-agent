/**
 * Screen 10 · Paper detail (step 10d): one paper, with Eureka's reading of it.
 *
 * Metadata and links come from OpenAlex / arXiv records. Below: the findings, limitations and future work the
 * Paper Reader found, each with its quote and whether that quote matched the paper's text word for word; the
 * methods and data; and where the user's own investigations used the paper (others' never show).
 */
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { Card, ErrorNote, MonoLabel, Pill, buttonClass } from "@/components/ui";
import { usePaper, type PaperDetail } from "@/lib/api/hooks";
import { words } from "@/lib/format";

const ROLE_TONES = { found: "neutral", read: "green", "evidence for a claim": "indigo", "closest work": "red" } as const;

export default function PaperPage() {
  const { id } = useParams<{ id: string }>();
  const paper = usePaper(id);
  if (paper.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (paper.error) {
    return (
      <div className="flex max-w-xl flex-col gap-4">
        <ErrorNote error={paper.error} />
        <Link href="/library" className="body-s text-indigo">← The library</Link>
      </div>
    );
  }
  return <Paper paper={paper.data} />;
}

function Paper({ paper }: { paper: PaperDetail }) {
  const a = paper.analysis;
  const links = [["OpenAlex", paper.links.openalex], ["arXiv", paper.links.arxiv], ["DOI", paper.links.doi], ["Open-access PDF", paper.links.open_access_pdf]]
    .filter((link): link is [string, string] => Boolean(link[1]));
  const verified = paper.quotes.filter((q) => q.verified).length;
  return (
    <div className="flex flex-col gap-7">
      <MonoLabel><Link href="/library" className="hover:text-ink">Library</Link> / <span className="text-ink">Paper</span></MonoLabel>
      <header className="flex flex-col gap-3.5">
        <div className="flex flex-wrap items-center gap-2.5">
          {paper.read ? <Pill tone="green">Read closely · {paper.source_level === "full_text" ? "full text" : "abstract"}</Pill> : <Pill>Found, not read</Pill>}
          {a?.contribution_type ? <Pill>{words(a.contribution_type)}</Pill> : null}
          <MonoLabel>{paper.peer_reviewed ? paper.venue ?? "peer-reviewed" : "preprint"} · {paper.year ?? "n.d."} · {paper.authors.length} author{paper.authors.length === 1 ? "" : "s"}</MonoLabel>
        </div>
        <h1 className="display-l max-w-4xl">{paper.title}</h1>
        {paper.authors.length ? <p className="body-s text-ink-2">{paper.authors.slice(0, 8).join(", ")}{paper.authors.length > 8 ? `, and ${paper.authors.length - 8} more` : ""}</p> : null}
        <div className="flex flex-wrap items-center gap-2.5">
          {links.map(([label, href]) => (
            <a key={label} href={href} target="_blank" rel="noreferrer" className={buttonClass(label === "OpenAlex" ? "ghost" : "secondary")}>{label} ↗</a>
          ))}
        </div>
        <p className="body-s max-w-3xl text-ink-3">
          Authors, venue and links come from OpenAlex and arXiv. Everything below is Eureka&apos;s reading, with each point tied
          to a quote checked against the paper&apos;s {paper.source_level === "full_text" ? "text" : "abstract"}.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="flex min-w-0 flex-col gap-6">
          {!a ? (
            <Card className="flex flex-col gap-3">
              <h2 className="heading-m">Not read closely yet</h2>
              <p className="body-m text-ink-2">Eureka found this paper but didn&apos;t read it: it wasn&apos;t among the most relevant in any investigation.</p>
              {paper.abstract ? <><MonoLabel>Abstract</MonoLabel><p className="body-m">{paper.abstract}</p></> : null}
            </Card>
          ) : (
            <>
              <Card className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="heading-m">Key findings</h2>
                  <span className="label-s text-[13px] text-green">{verified} / {paper.quotes.length} quotes verified</span>
                </div>
                {a.findings.length === 0 ? <p className="body-s text-ink-3">No findings were extracted.</p> : null}
                {a.findings.map((f, n) => (
                  <div key={n} className="flex gap-4 border-t border-line pt-3.5">
                    <span className="font-serif text-[28px] italic leading-7 text-accent">{n + 1}</span>
                    <div className="flex flex-col gap-2">
                      <p className="heading-s">{f.text}</p>
                      {f.setting ? <p className="body-s text-ink-3">Setting: {f.setting}</p> : null}
                      <Quote text={f.quote} verified={f.verified} />
                    </div>
                  </div>
                ))}
              </Card>
              <div className="grid gap-6 md:grid-cols-2">
                <Card className="flex flex-col gap-3 p-6">
                  <MonoLabel>Methods & data</MonoLabel>
                  {[["Task", a.tasks], ["Methods", a.methods], ["Data", a.datasets], ["Metrics", a.metrics], ["Baselines", a.baselines]]
                    .filter(([, list]) => (list as string[]).length).map(([label, list]) => (
                      <div key={label as string} className="flex gap-3 body-s">
                        <span className="w-20 shrink-0 text-ink-3">{label}</span>
                        <span>{(list as string[]).join(", ")}</span>
                      </div>
                    ))}
                  {a.setting ? <p className="body-s text-ink-2">{a.setting}</p> : null}
                </Card>
                <Points title="Limitations & gaps" items={a.limitations}
                  note="Gaps like these feed the open problems Eureka mines in “Find me an idea”." />
              </div>
              {a.future_work.length ? <Points title="Future work the authors suggest" items={a.future_work} /> : null}
              {paper.abstract ? (
                <Card className="flex flex-col gap-2 p-6"><MonoLabel>Abstract</MonoLabel><p className="body-m text-ink-2">{paper.abstract}</p></Card>
              ) : null}
            </>
          )}
        </div>

        <aside className="flex flex-col gap-6">
          <Card className="flex flex-col gap-2.5 p-6">
            <MonoLabel>At a glance</MonoLabel>
            <Row label="Published">{paper.year ?? "n.d."}{paper.venue ? ` · ${paper.venue}` : ""}</Row>
            <Row label="Cited by">{paper.citation_count ?? "unknown"}{paper.citation_count !== null ? " (OpenAlex)" : ""}</Row>
            <Row label="Read from">{paper.read ? (paper.source_level === "full_text" ? "full text" : "abstract") : "not read"}</Row>
            {a ? <Row label="Read on">{new Date(a.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</Row> : null}
          </Card>
          <Card className="flex flex-col gap-3 p-6">
            <MonoLabel>Used in your investigations</MonoLabel>
            {paper.used_in.length === 0 ? <p className="body-s text-ink-3">Not in any of your investigations.</p> : null}
            {paper.used_in.map((use) => (
              <Link key={use.investigation_id} href={`/investigations/${use.investigation_id}`} className="flex flex-col gap-1.5 rounded-md bg-paper px-3.5 py-3 hover:bg-surface-muted">
                <span className="heading-s text-sm">{use.title}</span>
                <span className="flex flex-wrap gap-1.5">
                  {use.roles.map((role) => <Pill key={role} tone={ROLE_TONES[role as keyof typeof ROLE_TONES] ?? "neutral"}>{role}</Pill>)}
                </span>
              </Link>
            ))}
          </Card>
        </aside>
      </div>
    </div>
  );
}

function Quote({ text, verified }: { text: string; verified: boolean }) {
  if (!text) return null;
  return (
    <div className="flex flex-col gap-2 rounded-md bg-paper px-3.5 py-2.5 sm:flex-row sm:items-center sm:gap-3">
      <span aria-hidden className={verified ? "hidden h-5 w-[3px] shrink-0 bg-green sm:block" : "hidden h-5 w-[3px] shrink-0 bg-amber sm:block"} />
      <span className="flex-1 font-serif text-[17px] italic text-ink-2">“{text}”</span>
      <Pill tone={verified ? "green" : "amber"}>{verified ? "verified word for word" : "not verified"}</Pill>
    </div>
  );
}

function Points({ title, items, note }: { title: string; items: { text: string; quote: string; verified: boolean }[]; note?: string }) {
  return (
    <Card className="flex flex-col gap-3 p-6">
      <MonoLabel>{title}</MonoLabel>
      {items.length === 0 ? <p className="body-s text-ink-3">None recorded.</p> : null}
      {items.map((item, n) => (
        <div key={n} className="flex flex-col gap-1.5">
          <p className="body-s"><span className="text-amber">— </span>{item.text}</p>
          {item.quote ? <span className="body-s font-serif italic text-ink-3">“{item.quote}” {item.verified ? "✓" : ""}</span> : null}
        </div>
      ))}
      {note ? <p className="body-s text-ink-3">{note}</p> : null}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 body-s">
      <span className="text-ink-3">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}
