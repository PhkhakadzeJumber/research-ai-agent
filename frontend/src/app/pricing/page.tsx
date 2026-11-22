/**
 * The Pricing page (step P4): public, so it sits outside the (app) group's login check. Logged in, it shows the
 * usual top nav; logged out, a small header with Log in and Sign up.
 */
import type { Metadata } from "next";
import { Suspense } from "react";

import { PricingShell } from "@/components/pricing-shell";
import { PricingScreen } from "@/components/pricing";

export const metadata: Metadata = { title: "Pricing" };

export default function Page() {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center body-s text-ink-3">Loading…</div>}>
      <PricingShell>
        <PricingScreen />
      </PricingShell>
    </Suspense>
  );
}
