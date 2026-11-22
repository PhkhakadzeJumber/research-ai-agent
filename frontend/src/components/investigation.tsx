/**
 * One investigation (step 10b): the page shows the view that fits its state.
 *   waiting for the user  → the clarification (screen 02)
 *   running, or stopped before the results → the progress (screen 03), with Stop / Resume
 *   results ready         → the results (screen 04 / the idea's detail: step 10c; a short summary until then)
 */
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ClarificationView } from "@/components/clarification";
import { ProgressView } from "@/components/progress";
import { Card, ErrorNote, MonoLabel, buttonClass } from "@/components/ui";
import { API } from "@/lib/api/client";
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
  return (
    <Card className="flex max-w-2xl flex-col gap-4">
      <MonoLabel>Results ready</MonoLabel>
      <h1 className="display-m">{data.title}</h1>
      <p className="body-m text-ink-2">
        {data.summary} · cost ${data.cost_usd.toFixed(2)}. The results page arrives in step 10c; meanwhile the full
        report is ready to download.
      </p>
      <div>
        <a href={`${API}/investigations/${data.id}/report`} className={buttonClass("primary")}>Download the report (Markdown)</a>
      </div>
    </Card>
  );
}
