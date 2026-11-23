/**
 * Screen 13 · Plan & billing (step P4; docs/18): the plan and its renewal, credits left (this month's and top-ups),
 * and the credit history: what each action was charged, or reserved while it runs. Paying (Manage billing, top-ups)
 * comes with payments (step P5); until then those buttons say so.
 */
"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, ButtonLink, Card, ErrorNote, MonoLabel, Pill, cx, type Tone } from "@/components/ui";
import { useBilling, usePlans, type Billing } from "@/lib/api/hooks";

const STATUS_TONES: Record<string, Tone> = { granted: "green", reserved: "spark", charged: "neutral", refunded: "indigo", free: "green" };
const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

export function BillingScreen() {
  const billing = useBilling();
  if (billing.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (billing.error) return <ErrorNote error={billing.error} />;
  const b = billing.data;
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[420px_1fr]">
      <div className="flex flex-col gap-6">
        <PlanCard billing={b} />
        {b.plan === "pro" ? <CreditsCard billing={b} /> : null}
      </div>
      <History billing={b} />
    </div>
  );
}

/** Opens checkout or Manage billing; where payments aren't open for this account, says so instead. */
function PayButton({ label, kind, href, open }: { label: string; kind: "accent" | "secondary"; href: string; open: boolean }) {
  const [asked, setAsked] = useState(false);
  if (open) return <ButtonLink href={href} kind={kind} className="w-fit">{label}</ButtonLink>;
  return (
    <span className="flex flex-col gap-1.5">
      <Button kind={kind} className="w-fit" onClick={() => setAsked(true)}>{label}</Button>
      {asked ? <span role="status" className="body-s text-ink-3">Payments aren&apos;t open yet.</span> : null}
    </span>
  );
}

function PlanCard({ billing: b }: { billing: Billing }) {
  if (b.plan === "free") {
    const left = b.free_evaluations_left ?? 0;
    return (
      <Card className="flex flex-col gap-4 p-7">
        <div className="flex items-center gap-2.5"><MonoLabel>Your plan</MonoLabel><Pill tone="green">Free</Pill></div>
        <span className="display-l">Free</span>
        <p className="body-s text-ink-2">
          {left > 0 ? `${left} full evaluation${left === 1 ? "" : "s"} left this month.` : "This month's free evaluation is used; it comes back on the 1st."}{" "}
          Pro adds finding ideas, research packages and {b.prices.evaluate}-credit evaluations whenever you need them.
        </p>
        <div className="flex flex-wrap gap-2.5">
          <ButtonLink href="/pricing" kind="accent" className="w-fit">See Pro →</ButtonLink>
          {b.checkout_open ? <ButtonLink href="/profile/billing/manage" kind="secondary" className="w-fit">Manage billing</ButtonLink> : null}
        </div>
      </Card>
    );
  }
  return (
    <Card dark className="flex flex-col gap-4 p-7">
      <div className="flex items-center gap-2.5"><MonoLabel className="text-accent">Your plan</MonoLabel><Pill tone="green">Active</Pill></div>
      <span className="display-l">Pro</span>
      <p className="body-s text-ink-3">
        {b.plan_until ? (b.plan_renews ? `Renews on ${day(b.plan_until)}. ` : `Cancelled: Pro stays until ${day(b.plan_until)}. `) : ""}New credits arrive on the 1st of every month.
      </p>
      <div className="flex flex-wrap gap-2.5"><ButtonLink href="/profile/billing/manage" kind="accent" className="w-fit">Manage billing</ButtonLink></div>
      <p className="body-s text-ink-3">Payments, invoices and your card are handled by our payment provider.</p>
    </Card>
  );
}

function Meter({ label, value, of, tone }: { label: string; value: string; of: number; tone: string }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between body-s"><span className="text-ink-2">{label}</span><span>{value}</span></div>
      <div className="h-2 overflow-hidden rounded-pill bg-surface-muted"><div className={cx("h-full rounded-pill", tone)} style={{ width: `${Math.min(100, of * 100)}%` }} /></div>
    </div>
  );
}

function CreditsCard({ billing: b }: { billing: Billing }) {
  const c = b.credits;
  const monthly = usePlans().data?.pro_monthly_credits ?? Math.max(c.monthly, 1);
  const evaluations = Math.floor(c.available / b.prices.evaluate);
  return (
    <Card className="flex flex-col gap-4 p-7">
      <MonoLabel>Credits left</MonoLabel>
      <div className="flex items-baseline gap-2"><span className="display-l">{c.available}</span><span className="body-s text-ink-3">credits</span></div>
      <Meter label="This month" value={`${c.monthly} left · resets on the 1st`} of={c.monthly / monthly} tone="bg-accent" />
      {c.other ? <Meter label="Top-ups and extra" value={`${c.other} left`} of={1} tone="bg-indigo" /> : null}
      {c.held ? <p className="body-s text-ink-2">{c.held} reserved by a run in progress: what it doesn&apos;t use comes back.</p> : null}
      <p className="body-s text-ink-2">
        Monthly credits are used first. Enough for about {evaluations} evaluation{evaluations === 1 ? "" : "s"}
        {c.available >= b.prices.discover ? `, or a discovery (${b.prices.discover})` : ""}.
      </p>
      <PayButton label="Buy more credits" kind="secondary" href="/checkout?product=topup" open={b.checkout_open} />
    </Card>
  );
}

function History({ billing: b }: { billing: Billing }) {
  return (
    <Card className="flex flex-col gap-1 p-7">
      <h2 className="heading-m pb-3">Credit history</h2>
      {b.history.length === 0 ? <p className="body-s text-ink-3">Nothing yet.</p> : null}
      <ul className="flex flex-col">
        {b.history.map((entry, n) => {
          const inner = (
            <>
              <span className="w-24 shrink-0 body-s text-ink-3">{day(entry.at)}</span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="body-m truncate">{entry.title}</span>
                <span className="body-s text-ink-3">{entry.detail}</span>
              </span>
              <Pill tone={STATUS_TONES[entry.status] ?? "neutral"}>{entry.status}</Pill>
              <span className={cx("w-14 shrink-0 text-right font-mono text-sm", entry.credits > 0 && "text-green")}>
                {entry.credits > 0 ? `+${entry.credits}` : entry.credits === 0 ? "0" : `−${-entry.credits}`}
              </span>
            </>
          );
          const row = "flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line py-3.5";
          return (
            <li key={`${entry.at}-${n}`}>
              {entry.investigation_id
                ? <Link href={`/investigations/${entry.investigation_id}`} className={cx(row, "hover:bg-paper")}>{inner}</Link>
                : <div className={row}>{inner}</div>}
            </li>
          );
        })}
      </ul>
      <p className="pt-3 body-s text-ink-3">You&apos;re charged what a run actually used, never more than the price shown before it started.</p>
    </Card>
  );
}
