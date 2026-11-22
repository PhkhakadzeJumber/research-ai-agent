/**
 * Screen 07 · Investigations (step 10c): everything Eureka evaluated or discovered for the user, newest first, with
 * its state and cost; filters for running and completed; this month's spend.
 */
"use client";

import Link from "next/link";
import { useState } from "react";

import { ButtonLink, Card, ErrorNote, Pill, cx } from "@/components/ui";
import { useInvestigations, useMe } from "@/lib/api/hooks";
import { statusOf, whenText } from "@/lib/format";

const FILTERS = [
  { status: null, label: "All" },
  { status: "running", label: "In progress" }, // the API's "running" includes paused runs
  { status: "completed", label: "Completed" },
] as const;

export default function InvestigationsPage() {
  const [status, setStatus] = useState<string | null>(null);
  const list = useInvestigations(status);
  const me = useMe();
  const items = list.data?.items ?? [];

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2.5">
          <h1 className="display-l">Your <em>investigations</em></h1>
          <p className="body-m text-ink-2">Everything Eureka has evaluated or discovered for you, with the credits each used.</p>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <ButtonLink href="/" kind="secondary">Find me an idea</ButtonLink>
          <ButtonLink href="/">+ Evaluate an idea</ButtonLink>
        </div>
      </header>

      <div className="flex gap-2" role="tablist" aria-label="Filter">
        {FILTERS.map((f) => (
          <button key={f.label} role="tab" aria-selected={status === f.status} onClick={() => setStatus(f.status)}
            className={cx("rounded-pill px-3.5 py-1.5 label-s text-[13px]", status === f.status ? "bg-ink text-paper" : "border border-line bg-surface text-ink-2")}>
            {f.label}{status === f.status && list.data ? ` · ${list.data.total}` : ""}
          </button>
        ))}
      </div>

      {list.error ? <ErrorNote error={list.error} /> : null}
      <Card className="overflow-hidden p-0">
        <div className="hidden grid-cols-[1fr_120px_220px_90px_110px_24px] gap-4 px-6 py-3.5 label-mono text-ink-3 md:grid">
          <span>Investigation</span><span>Mode</span><span>Status</span><span>Credits</span><span>Updated</span><span />
        </div>
        {list.isPending ? <p className="body-s border-t border-line px-6 py-5 text-ink-3">Loading…</p> : null}
        {!list.isPending && items.length === 0 ? (
          <div className="flex flex-col gap-2 border-t border-line px-6 py-8">
            <span className="heading-s">Nothing here yet</span>
            <span className="body-s text-ink-2">
              Start with an idea you have, or let Eureka find one: <Link href="/" className="text-indigo">start here</Link>.
            </span>
          </div>
        ) : null}
        {items.map((item) => {
          const [label, tone] = statusOf(item);
          return (
            <Link key={item.id} href={`/investigations/${item.id}`}
              className="grid grid-cols-1 items-center gap-x-4 gap-y-2 border-t border-line px-6 py-4 hover:bg-paper md:grid-cols-[1fr_120px_220px_90px_110px_24px]">
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="heading-s truncate">{item.title}</span>
                <span className="body-s truncate text-ink-3">{item.summary}</span>
              </span>
              <span className="hidden md:block"><Pill tone={item.mode === "discover" ? "violet" : "indigo"}>{item.mode === "discover" ? "Mode 2" : "Mode 1"}</Pill></span>
              <span><Pill tone={tone}>{label}</Pill></span>
              <span className="hidden font-mono text-sm md:block">{item.credits.free ? "free" : `${item.credits.used} cr`}</span>
              <span className="hidden body-s text-ink-3 md:block">{whenText(item.updated_at)}</span>
              <span aria-hidden className="hidden text-ink-3 md:block">→</span>
            </Link>
          );
        })}
      </Card>

      {me.data ? (
        <Card tone="accent" className="flex flex-wrap items-center gap-4 p-5">
          <span aria-hidden className="text-2xl">✦</span>
          <p className="body-m flex-1">
            {me.data.plan.plan === "pro"
              ? `Pro · ${me.data.plan.credits.available} credits left. A run never costs more than the price shown when it starts, and you can resume any paused one.`
              : `Free plan · ${me.data.plan.free_evaluations_left ?? 0} evaluation(s) left this month. Pro adds discovery and research packages.`}
          </p>
          <Link href="/profile/billing" className="body-s font-semibold text-indigo">Plan & billing →</Link>
        </Card>
      ) : null}
    </div>
  );
}
