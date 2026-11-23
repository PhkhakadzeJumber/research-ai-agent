/** Step C: Checkout. */
import type { Metadata } from "next";
import { Suspense } from "react";

import { CheckoutScreen } from "@/components/checkout";

export const metadata: Metadata = { title: "Checkout" };

export default function Page() {
  return (
    <Suspense>
      <CheckoutScreen />
    </Suspense>
  );
}
