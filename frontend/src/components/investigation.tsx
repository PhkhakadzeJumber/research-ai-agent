/**
 * One investigation: /investigations/{id} opens its step page (Figma pages 3 and 4; components/steps.tsx): the step
 * working or waiting for the user, else the last finished one (the verdict in Mode 1, the ranking in Mode 2).
 */
"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { ErrorNote } from "@/components/ui";
import { useSteps } from "@/lib/api/hooks";
import { landingStep, stepHref } from "@/lib/steps";

export const FINISHED_STAGES = ["results_ready", "writing_proposal"];

export function InvestigationScreen() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const steps = useSteps(id, false);

  useEffect(() => {
    if (steps.data) router.replace(stepHref(id, landingStep(steps.data)));
  }, [steps.data, id, router]);

  if (steps.error) {
    return (
      <div className="flex max-w-xl flex-col gap-4">
        <ErrorNote error={steps.error} />
        <Link href="/investigations" className="body-s text-indigo">← Your investigations</Link>
      </div>
    );
  }
  return <p className="body-s text-ink-3">Loading…</p>;
}
