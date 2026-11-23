/**
 * Screens 17 and 18 · Checkout and Payment success (step C; Figma): the card form and the order summary, then the
 * welcome. The card number is checked and kept in the browser (lib/card.ts); the server gets only the brand, the
 * last four digits and the expiry, and decides whether this account can buy (src/api/routes/billing.py).
 */
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button, ButtonLink, Card, ErrorNote, Field, Input, MonoLabel, Pill, cx } from "@/components/ui";
import { useCheckout, useMe, usePlans, type Plans } from "@/lib/api/hooks";
import { expiryValid, formatExpiry, formatNumber, luhn, receiptOf } from "@/lib/card";

export type Product = "pro_monthly" | "pro_yearly" | "topup";
const usd = (n: number) => `$${n.toFixed(2)}`;

function productOf(text: string | null): Product {
  return text === "pro_monthly" || text === "topup" ? text : "pro_yearly";
}

export function priceOf(plans: Plans, product: Product): number {
  return product === "pro_monthly" ? plans.pro_monthly_usd : product === "pro_yearly" ? plans.pro_yearly_usd : plans.topup_usd;
}

export function CheckoutScreen() {
  const plans = usePlans();
  const me = useMe();
  const [product, setProduct] = useState<Product>(productOf(useSearchParams().get("product")));
  if (plans.isPending || me.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (plans.error) return <ErrorNote error={plans.error} />;
  if (me.data && !me.data.plan.checkout_open) {
    return (
      <Card className="flex max-w-xl flex-col gap-3 p-8">
        <h1 className="display-m">Payments aren&apos;t open yet</h1>
        <p className="body-m text-ink-2">You can keep using your free evaluation every month in the meantime.</p>
        <ButtonLink href="/pricing" kind="secondary" className="w-fit">Back to plans</ButtonLink>
      </Card>
    );
  }
  const topup = product === "topup";
  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-2.5">
        <MonoLabel><Link href="/pricing" className="hover:text-ink">Pricing</Link> / <span className="text-ink">Checkout</span></MonoLabel>
        <h1 className="display-l">{topup ? <>Buy more <em>credits</em></> : <>Upgrade to <em>Pro</em></>}</h1>
      </header>
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_420px]">
        <PaymentForm product={product} amount={priceOf(plans.data, product)} email={me.data?.email ?? ""} />
        <Summary plans={plans.data} product={product} setProduct={setProduct} />
      </div>
    </div>
  );
}

function PaymentForm({ product, amount, email }: { product: Product; amount: number; email: string }) {
  const router = useRouter();
  const checkout = useCheckout();
  const [receiptEmail, setReceiptEmail] = useState(email);
  const [number, setNumber] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvc, setCvc] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("Georgia");
  const [touched, setTouched] = useState(false);
  const problems = [
    !luhn(number) && "Check the card number.",
    !expiryValid(expiry) && "Check the expiry date (MM / YY).",
    !/^\d{3,4}$/.test(cvc) && "The CVC is the 3 or 4 digits on the back.",
    !name.trim() && "Add the name on the card.",
  ].filter(Boolean) as string[];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (problems.length) return;
    try {
      await checkout.mutateAsync({ product, ...receiptOf(number, expiry) });
      router.push(`/checkout/success?product=${product}`);
    } catch { /* shown by ErrorNote */ }
  }

  return (
    <Card className="flex flex-col gap-5 p-7">
      <h2 className="heading-m">Payment details</h2>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Email for receipts">
          <Input type="email" value={receiptEmail} onChange={(e) => setReceiptEmail(e.target.value)} autoComplete="email" />
        </Field>
        <Field label="Card number">
          <Input inputMode="numeric" autoComplete="cc-number" placeholder="1234 1234 1234 1234" value={number}
            onChange={(e) => setNumber(formatNumber(e.target.value))} />
        </Field>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Expiry">
            <Input inputMode="numeric" autoComplete="cc-exp" placeholder="MM / YY" value={expiry}
              onChange={(e) => setExpiry(formatExpiry(e.target.value))} />
          </Field>
          <Field label="CVC">
            <Input inputMode="numeric" autoComplete="cc-csc" placeholder="123" value={cvc}
              onChange={(e) => setCvc(e.target.value.replace(/\D/g, "").slice(0, 4))} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Name on card">
            <Input autoComplete="cc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Country">
            <Input autoComplete="country-name" value={country} onChange={(e) => setCountry(e.target.value)} />
          </Field>
        </div>
        {touched && problems.length ? <p role="alert" className="rounded-md bg-red-soft px-4 py-3 body-s text-red">{problems[0]}</p> : null}
        <ErrorNote error={checkout.error} />
        <Button type="submit" kind="accent" disabled={checkout.isPending} className="w-full">
          {checkout.isPending ? "Paying…" : `Pay ${usd(amount)} →`}
        </Button>
        <p className="body-s text-ink-3">🔒 Payments are encrypted and handled by our payment provider. Eureka never sees your full card number.</p>
      </form>
    </Card>
  );
}

function Summary({ plans, product, setProduct }: { plans: Plans; product: Product; setProduct: (p: Product) => void }) {
  const topup = product === "topup";
  const amount = priceOf(plans, product);
  const save = Math.round((1 - plans.pro_yearly_usd / (12 * plans.pro_monthly_usd)) * 100);
  return (
    <Card dark className="flex flex-col gap-4 p-7">
      <div className="flex items-center gap-2.5">
        <MonoLabel className="text-accent">Order</MonoLabel>
        {product === "pro_yearly" ? <Pill tone="spark">Yearly · save {save}%</Pill> : null}
      </div>
      <h2 className="display-m">{topup ? `${plans.topup_credits} credits` : "Eureka Pro"}</h2>
      <p className="body-s text-ink-3">
        {topup ? "Added to your account at once, valid for 12 months. Monthly credits are used first."
          : `${plans.pro_monthly_credits} credits every month, discovery and research packages. Renews ${product === "pro_yearly" ? "yearly" : "monthly"}; cancel any time.`}
      </p>
      {topup ? null : (
        <div role="group" aria-label="Billing period" className="flex w-fit gap-1 rounded-pill bg-white/10 p-1">
          {([["pro_monthly", `Monthly · ${usd(plans.pro_monthly_usd).replace(".00", "")}`], ["pro_yearly", `Yearly · ${usd(plans.pro_yearly_usd).replace(".00", "")}`]] as const).map(([value, label]) => (
            <button key={value} type="button" aria-pressed={product === value} onClick={() => setProduct(value)}
              className={cx("rounded-pill px-3.5 py-2 label-s", product === value ? "bg-accent text-ink" : "text-paper")}>{label}</button>
          ))}
        </div>
      )}
      <dl className="flex flex-col gap-2.5 pt-1 body-s">
        <div className="flex justify-between text-ink-3"><dt>{topup ? "Top-up" : product === "pro_yearly" ? "Pro, 12 months" : "Pro, 1 month"}</dt><dd className="text-paper">{usd(amount)}</dd></div>
        <div className="flex justify-between text-ink-3"><dt>Tax</dt><dd className="text-paper">included</dd></div>
        <div className="flex justify-between border-t border-white/15 pt-2.5 heading-s"><dt>Due today</dt><dd>{usd(amount)}</dd></div>
      </dl>
      <p className="body-s text-ink-3">
        By paying you agree to the <Link href="/terms" className="underline">Terms of Service</Link> and the{" "}
        <Link href="/refunds" className="underline">Refund Policy</Link>.
      </p>
    </Card>
  );
}

export function SuccessScreen() {
  const product = productOf(useSearchParams().get("product"));
  const me = useMe();
  const plan = me.data?.plan;
  const topup = product === "topup";
  const until = plan?.plan_until ? new Date(plan.plan_until).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : null;
  return (
    <div className="flex justify-center pt-6">
      <Card className="flex w-full max-w-[560px] flex-col items-center gap-4 p-11 text-center">
        <span aria-hidden className="grid size-16 place-items-center rounded-full bg-green-soft text-3xl text-green">✓</span>
        <h1 className="display-l">{topup ? <>Credits <em>added</em></> : <>Welcome to <em>Pro</em></>}</h1>
        <p className="body-m max-w-md text-ink-2">
          {topup ? "Your credits are ready to use." : "Find an idea, evaluate as many as your credits allow, and write research packages."}
        </p>
        {plan ? (
          <dl className="flex w-full flex-col gap-2.5 rounded-md bg-paper px-5 py-4 text-left body-s">
            <div className="flex justify-between"><dt className="text-ink-2">Plan</dt><dd>{plan.plan === "pro" ? "Pro" : "Free"}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-2">Credits</dt><dd>{plan.credits.available} available</dd></div>
            {until ? <div className="flex justify-between"><dt className="text-ink-2">{plan.plan_renews ? "Renews" : "Pro until"}</dt><dd>{until}</dd></div> : null}
            <div className="flex justify-between"><dt className="text-ink-2">Receipt</dt><dd>in <Link href="/profile/billing/manage" className="text-indigo">Manage billing</Link></dd></div>
          </dl>
        ) : null}
        <div className="flex flex-wrap justify-center gap-2.5">
          <ButtonLink href="/" kind="accent">{topup ? "Start an investigation →" : "Find me an idea →"}</ButtonLink>
          <ButtonLink href="/profile/billing" kind="secondary">Plan &amp; billing</ButtonLink>
        </div>
      </Card>
    </div>
  );
}
