/**
 * One investigation (step 10b): the page shows the view that fits its state.
 *   waiting for the user  → the clarification (screen 02)
 *   running, or stopped before the results → the progress (screen 03), with Stop / Resume
 *   results ready         → the results (screen 04 in Mode 2; the idea's detail, screen 05, in Mode 1)
 */
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ClarificationView } from "@/components/clarification";
import { ProgressView } from "@/components/progress";
import { ResultsView } from "@/components/results";
import { ErrorNote } from "@/components/ui";
import { useInvestigation } from "@/lib/api/hooks";

export const FINISHED_STAGES = ["results_ready", "writing_proposal"];

export function InvestigationScreen() {
  const { id } = useParams<{ id: string }>();
  const investigation = useInvestigation(id);

  if (investigation.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (investigation.error) {
    return (
      <div className="flex max-w-xl flex-col gap-4">
        <ErrorNote error={investigation.error} />
        <Link href="/investigations" className="body-s text-indigo">← Your investigations</Link>
      </div>
    );
  }
  const data = investigation.data;
  if (data.waiting_for === "clarification") return <ClarificationView investigation={data} />;
  if (!FINISHED_STAGES.includes(data.stage)) return <ProgressView investigation={data} />;
  return <ResultsView investigation={data} />;
}
