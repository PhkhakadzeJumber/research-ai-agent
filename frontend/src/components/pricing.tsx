/**
 * Screen 12 · Pricing (step P4; docs/18): Free and Pro, what credits buy, and the questions people ask. Public: it
 * reads GET /plans (no login), so the numbers are the server's. Buying Pro comes with payments (step P5); until
 * then the Pro button says so.
 */
"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, ButtonLink, Card, ErrorNote, MonoLabel, Pill, cx } from "@/components/ui";
import { useMe, usePlans, type Plans } from "@/lib/api/hooks";

const usd = (n: number) => `$${Number.isInteger(n) ? n : n.toFixed(2)}`;

export function PricingScreen() {
  const plans = usePlans();
  const me = useMe();
  const [yearly, setYearly] = useState(true);
  if (plans.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (plans.error) return <ErrorNote error={plans.error} />;
  const p = plans.data;
  const current = me.data?.plan.plan;
  return (
    <div className="flex flex-col items-center gap-10">
      <header className="flex max-w-2xl flex-col items-center gap-3.5 text-center">
        <MonoLabel>Plans</MonoLabel>
        <h1 className="display-l">Good research shouldn&apos;t <em>cost a fortune.</em></h1>
        <p className="body-m text-ink-2">
          Evaluate an idea for free every month. Go Pro to discover ideas and write research packages. Every price is
          shown before a run starts, and you never pay more.
        </p>
        <div role="group" aria-label="Billing period" className="flex gap-1 rounded-pill bg-surface-muted p-1">
          {[[false, "Monthly"], [true, `Yearly · save ${Math.round((1 - p.pro_yearly_usd / (12 * p.pro_monthly_usd)) * 100)}%`]].map(([value, label]) => (
            <button key={String(value)} type="button" aria-pressed={yearly === value} onClick={() => setYearly(value as boolean)}
              className={cx("rounded-pill px-4 py-2 label-s", yearly === value ? "bg-ink text-paper" : "text-ink-2")}>
              {label as string}
            </button>
          ))}
        </div>
      </header>

      <div className="grid w-full max-w-[864px] gap-6 md:grid-cols-2">
        <PlanCard name="Free" price="$0" per="forever" note="Try Eureka on your own idea, with the full evaluation."
          features={[[true, `${p.free_evaluations} idea evaluation${p.free_evaluations === 1 ? "" : "s"} every month`],
            [true, "Upload your draft or proposal (private)"], [true, "Library, results and Markdown export"],
            [false, "Find me an idea"], [false, "Research packages"]]}
          action={current ? <Pill tone={current === "free" ? "green" : "neutral"}>{current === "free" ? "Your plan" : "Included"}</Pill>
            : <ButtonLink href="/signup" kind="secondary">Start free</ButtonLink>} />
        <PlanCard dark name="Pro" price={usd(yearly ? p.pro_yearly_usd / 12 : p.pro_monthly_usd)}
          per={yearly ? `/ month, billed ${usd(p.pro_yearly_usd)} yearly` : "/ month"}
          note={yearly ? `Or ${usd(p.pro_monthly_usd)} month to month.` : `Or ${usd(p.pro_yearly_usd)} for a year.` + ""}
          features={[[true, `${p.pro_monthly_credits} credits every month`], [true, `Evaluate ideas: ${p.prices.evaluate} credits each`],
            [true, `Find me an idea: ${p.prices.discover} credits`], [true, `Research packages: ${p.prices.package} credits each`],
            [true, `Top up any time: ${p.topup_credits} credits for ${usd(p.topup_usd)}`]]}
          action={current === "pro" ? <Pill tone="green">Your plan</Pill> : <GoPro product={yearly ? "pro_yearly" : "pro_monthly"} open={Boolean(me.data?.plan.checkout_open)} loggedIn={Boolean(me.data)} />} />
      </div>

      <CreditTable plans={p} />

      <div className="grid w-full max-w-[864px] gap-6 md:grid-cols-3">
        {[["Why not unlimited?", "Every run reads dozens of papers with a top AI model. Credits keep the price low and fair: you pay for what you use."],
          ["Is my document safe?", "Uploads stay private to your investigation, are never added to the library, and are sent to the AI provider only with your consent."],
          ["What if a run fails?", "If it fails on our side, every credit comes back. If you stop it, you pay only for what it used."]].map(([q, a]) => (
          <Card key={q} className="flex flex-col gap-2.5 p-6">
            <h2 className="heading-s">{q}</h2>
            <p className="body-s text-ink-2">{a}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}

function GoPro({ product, open, loggedIn }: { product: string; open: boolean; loggedIn: boolean }) {
  const [asked, setAsked] = useState(false);
  if (open) return <ButtonLink href={`/checkout?product=${product}`} kind="accent" className="w-fit">Go Pro →</ButtonLink>;
  if (!loggedIn) return <ButtonLink href={`/signup?next=${encodeURIComponent("/pricing")}`} kind="accent" className="w-fit">Go Pro →</ButtonLink>;
  return (
    <div className="flex flex-col gap-2">
      <Button kind="accent" className="w-fit" onClick={() => setAsked(true)}>Go Pro →</Button>
      {asked ? <p role="status" className="body-s text-ink-3">Payments aren&apos;t open yet.</p> : null}
    </div>
  );
}

function PlanCard({ name, price, per, note, features, action, dark = false }: {
  name: string; price: string; per: string; note: string; features: [boolean, string][]; action: React.ReactNode; dark?: boolean;
}) {
  return (
    <Card dark={dark} className="flex flex-col gap-5 p-8">
      <div className="flex items-center gap-2.5">
        <MonoLabel className={dark ? "text-accent" : undefined}>{name}</MonoLabel>
        {dark ? <Pill tone="spark">For students & researchers</Pill> : null}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="display-l">{price}</span>
        <span className="body-s text-ink-3">{per}</span>
      </div>
      <p className={cx("body-s", dark ? "text-ink-3" : "text-ink-2")}>{note}</p>
      <ul className="flex flex-1 flex-col gap-3">
        {features.map(([ok, text]) => (
          <li key={text} className={cx("flex gap-2.5 body-m", !ok && "text-ink-3")}>
            <span aria-hidden className={ok ? (dark ? "text-accent" : "text-green") : ""}>{ok ? "✓" : "🔒"}</span>
            <span><span className="sr-only">{ok ? "Included: " : "Not included: "}</span>{text}</span>
          </li>
        ))}
      </ul>
      <div>{action}</div>
    </Card>
  );
}

function CreditTable({ plans }: { plans: Plans }) {
  const per = (price: number) => Math.floor(plans.pro_monthly_credits / price);
  const rows: [string, number, string, string][] = [
    ["Evaluate my idea", plans.prices.evaluate, "10–15 min", `${per(plans.prices.evaluate)} evaluations`],
    ["Find me an idea", plans.prices.discover, "25–40 min", `${per(plans.prices.discover)} discover${per(plans.prices.discover) === 1 ? "y" : "ies"} + more`],
    ["Research package", plans.prices.package, "~2 min", `${per(plans.prices.package)} packages`],
  ];
  return (
    <Card className="flex w-full max-w-[864px] flex-col gap-4 p-8">
      <h2 className="heading-m">What credits buy</h2>
      <p className="body-s text-ink-2">Each action shows its price before it starts. If a run costs less, or you stop it early, the unused credits go back.</p>
      <table className="w-full text-left">
        <thead>
          <tr className="label-mono text-ink-3">
            <th className="py-2 font-medium">Action</th><th className="py-2 font-medium">Credits</th>
            <th className="hidden py-2 font-medium sm:table-cell">Time</th><th className="py-2 font-medium">{plans.pro_monthly_credits} credits ≈</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([action, credits, time, buys]) => (
            <tr key={action} className="border-t border-line">
              <td className="py-3.5 heading-s">{action}</td><td className="py-3.5 body-m">{credits}</td>
              <td className="hidden py-3.5 body-m sm:table-cell">{time}</td><td className="py-3.5 body-m">{buys}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="body-s text-ink-3">
        Questions? <Link href="/#how-it-works" className="text-indigo">See how Eureka works</Link>.
      </p>
    </Card>
  );
}
