/** Step C: Welcome to Pro. */
import type { Metadata } from "next";
import { Suspense } from "react";

import { SuccessScreen } from "@/components/checkout";

export const metadata: Metadata = { title: "Welcome to Pro" };

export default function Page() {
  return (
    <Suspense>
      <SuccessScreen />
    </Suspense>
  );
}
