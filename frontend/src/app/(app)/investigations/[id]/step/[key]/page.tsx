/** One step of a run (Figma pages 3 and 4); see components/steps.tsx. */
import { Suspense } from "react";

import { StepScreen } from "@/components/steps";

export default function StepPage() {
  return (
    <Suspense fallback={<p className="body-s text-ink-3">Loading…</p>}>
      <StepScreen />
    </Suspense>
  );
}
