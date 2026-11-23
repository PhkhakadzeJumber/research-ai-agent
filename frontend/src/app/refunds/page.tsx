/** Screen 20 · Refund Policy (step D): public, in the same frame as the Pricing page. */
import type { Metadata } from "next";
import { Suspense } from "react";

import { LegalScreen } from "@/components/legal";
import { PricingShell } from "@/components/pricing-shell";

export const metadata: Metadata = { title: "Refund Policy" };

export default function Page() {
  return (
    <Suspense>
      <PricingShell>
        <LegalScreen page="refunds" />
      </PricingShell>
    </Suspense>
  );
}
