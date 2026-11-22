/** One investigation (step 10b): the view that fits its state; see components/investigation.tsx. */
import { Suspense } from "react";

import { InvestigationScreen } from "@/components/investigation";

export default function InvestigationPage() {
  return (
    <Suspense fallback={<p className="body-s text-ink-3">Loading…</p>}>
      <InvestigationScreen />
    </Suspense>
  );
}
