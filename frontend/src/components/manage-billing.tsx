/**
 * Screen 19 · Manage billing (step C; Figma): the subscription (cancel, or undo the cancel), the card on file and
 * the invoices, each downloadable as a receipt.
 */
"use client";

import Link from "next/link";

import { Button, ButtonLink, Card, ErrorNote, MonoLabel, Pill } from "@/components/ui";
import { API } from "@/lib/api/client";
import { useCancelPlan, useManage, useMe, useRenewPlan } from "@/lib/api/hooks";

const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

export function ManageScreen() {
  const manage = useManage();
  const open = useMe().data?.plan.checkout_open ?? false;
  const cancel = useCancelPlan();
  const renew = useRenewPlan();
  if (manage.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (manage.error) return <ErrorNote error={manage.error} />;
  const m = manage.data;
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[420px_1fr]">
      <div className="flex flex-col gap-6">
        <Card className="flex flex-col gap-3.5 p-7">
          <MonoLabel>Subscription</MonoLabel>
          {m.plan === "pro" ? (
            <>
              <div className="flex items-center gap-2.5">
                <span className="heading-m">Pro{m.period ? ` · ${m.period}` : ""}</span>
                <Pill tone={m.plan_renews ? "green" : "amber"}>{m.plan_renews ? "Active" : "Cancelled"}</Pill>
              </div>
              <p className="body-s text-ink-2">
                {m.plan_until ? (m.plan_renews ? `Renews on ${day(m.plan_until)}.` : `Pro stays until ${day(m.plan_until)}, then the free plan.`) : null}
              </p>
              <ErrorNote error={cancel.error ?? renew.error} />
              {m.plan_renews ? (
                <Button kind="ghost" className="w-fit text-red" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
                  {cancel.isPending ? "Cancelling…" : "Cancel plan"}
                </Button>
              ) : (
                <Button kind="secondary" className="w-fit" disabled={renew.isPending} onClick={() => renew.mutate()}>
                  {renew.isPending ? "Saving…" : "Keep Pro (renew)"}
                </Button>
              )}
              <p className="body-s text-ink-3">If you cancel, Pro stays until its paid date; your unused credits stay until they expire.</p>
            </>
          ) : (
            <>
              <span className="heading-m">Free</span>
              <p className="body-s text-ink-2">One full evaluation every month.</p>
              <ButtonLink href="/pricing" kind="accent" className="w-fit">See Pro →</ButtonLink>
            </>
          )}
        </Card>
        <Card className="flex flex-col gap-3.5 p-7">
          <MonoLabel>Payment method</MonoLabel>
          {m.card ? (
            <div className="flex items-center gap-3.5">
              <span className="rounded-sm bg-ink px-2.5 py-2 label-mono text-paper">{m.card.brand === "Mastercard" ? "MC" : m.card.brand.toUpperCase()}</span>
              <span className="flex flex-1 flex-col">
                <span className="heading-s whitespace-nowrap">{m.card.brand} •••• {m.card.last4}</span>
                <span className="body-s text-ink-3">Expires {m.card.expiry}</span>
              </span>
              {open ? <ButtonLink href={`/checkout?product=${m.period === "monthly" ? "pro_monthly" : "pro_yearly"}`} kind="secondary">Update</ButtonLink> : null}
            </div>
          ) : <p className="body-s text-ink-2">No card on file.</p>}
        </Card>
      </div>
      <Card className="flex flex-col gap-1 p-7">
        <h2 className="heading-m pb-3">Invoices</h2>
        {m.invoices.length === 0 ? <p className="body-s text-ink-3">No invoices yet.</p> : null}
        <ul className="flex flex-col">
          {m.invoices.map((inv) => (
            <li key={inv.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line py-3.5">
              <span className="w-28 body-s text-ink-3">{day(inv.at)}</span>
              <span className="flex-1 body-m">{inv.description}<span className="block body-s text-ink-3">{inv.card}</span></span>
              <span className="font-mono text-sm">${inv.amount_usd.toFixed(2)}</span>
              <a href={`${API}/billing/invoices/${inv.id}`} className="body-s font-medium text-indigo">Download</a>
            </li>
          ))}
        </ul>
        <p className="pt-3 body-s text-ink-3">Receipts are also on this page any time. <Link href="/profile/billing" className="text-indigo">Back to Plan &amp; billing</Link></p>
      </Card>
    </div>
  );
}
