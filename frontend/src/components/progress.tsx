/**
 * Screen 03 · Progress (step 10b): the run as it happens.
 *
 * The stage rail, counters and cost come from GET /progress (read from the database, so always right); the activity
 * feed is the stored events plus the live stream (lib/live.ts). Each live event refreshes the summary (at most every
 * 2 s). A run that stopped (Stop, the budget, an account refusal, a failure, a server restart) shows why, and Resume
 * continues it, with a higher budget if the budget is what ran out.
 */
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import { Button, Card, ErrorNote, Input, MonoLabel, Pill, cx, type Tone } from "@/components/ui";
import {
  investigationKeys,
  useActivity,
  useProgress,
  useResume,
  useStop,
  type Investigation,
  type Progress,
  type ProgressEvent,
} from "@/lib/api/hooks";
import { useLiveEvents } from "@/lib/live";

const HEADLINES: Record<string, [string, string]> = {
  understanding: ["Eureka is ", "reading your request."],
  clarification: ["Waiting for ", "your answers."],
  searching: ["Searching ", "the literature."],
  screening: ["Screening ", "the papers."],
  reading: ["Reading ", "the closest papers."],
  novelty: ["Comparing ", "part by part."],
  skeptic: ["The ", "Skeptic is on it."],
  value: ["Judging ", "significance and feasibility."],
  verdicts: ["Reaching ", "the verdicts."],
  loop: ["Trying ", "refinements and alternatives."],
  landscape: ["Mapping ", "the landscape."],
  landscape_reading: ["Reading ", "the landscape."],
  mining: ["Finding ", "open problems."],
  ideas: ["Proposing ", "ideas."],
  light: ["Light evaluations ", "under way."],
  full: ["Full evaluations ", "under way."],
  results: ["Ranking ", "the results."],
};

const KIND_TONES: Record<string, Tone> = {
  stage: "indigo", step_started: "neutral", step_done: "green", candidate: "violet", warning: "amber", error: "red",
  ended: "spark",
};
const KIND_LABELS: Record<string, string> = {
  stage: "Stage", step_started: "Started", step_done: "Done", candidate: "Idea", warning: "Warning", error: "Problem",
  ended: "Ended",
};

export function headline(progress: Progress | undefined): [string, string] {
  if (!progress) return ["Getting ", "started."];
  const active = progress.rail.find((s) => s.status === "active" || s.status === "paused" || s.status === "failed");
  if (active?.status === "failed") return ["This run ", "stopped."];
  if (active?.status === "paused") return ["Paused at ", `${active.label.toLowerCase()}.`];
  return HEADLINES[active?.key ?? ""] ?? ["Working ", "on it."];
}

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function ProgressView({ investigation }: { investigation: Investigation }) {
  const id = investigation.id;
  const running = investigation.run !== "idle";
  const client = useQueryClient();
  const progress = useProgress(id, running);
  const activity = useActivity(id);
  const stop = useStop(id);
  const resume = useResume(id);
  const lastRefresh = useRef(0);

  const onEvent = useCallback((event: ProgressEvent) => {
    const now = Date.now();
    if (event.kind === "ended" || event.kind === "stage" || now - lastRefresh.current > 2_000) {
      lastRefresh.current = now;
      client.invalidateQueries({ queryKey: investigationKeys.progress(id) });
      if (event.kind === "ended" || event.kind === "stage") client.invalidateQueries({ queryKey: investigationKeys.detail(id) });
    }
  }, [client, id]);
  const { events, connected } = useLiveEvents(id, { initial: activity.data?.items, active: running, onEvent });
  const feed = useMemo(() => [...events].reverse(), [events]);

  const data = progress.data;
  const [plain, emphasis] = headline(data);
  const lastRun = investigation.last_run;
  const needsBudget = !running && lastRun?.reason === "budget";
  const stoppedEarly = !running && lastRun && ["stopped", "budget", "account_refused", "failed"].includes(lastRun.reason);
  const cost = data?.cost_usd ?? investigation.cost_usd;
  const cap = data?.budget_usd ?? investigation.budget_usd;
  const current = data?.rail.find((s) => s.status === "active");

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2.5">
        <MonoLabel>
          Investigations / {investigation.mode === "discover" ? "Find me an idea" : "Evaluate my idea"} /{" "}
          <span className="text-ink">Step 2 of 3 · {investigation.mode === "discover" ? "Discovering" : "Evaluating"}</span>
        </MonoLabel>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2.5">
            <h1 className="display-l">{plain}<em>{emphasis}</em></h1>
            <p className="body-m max-w-2xl text-ink-2">
              <span className="font-medium text-ink">{investigation.title}.</span>{" "}
              {running ? "You can leave this page: the run keeps going, and the results wait for you here." : "Nothing is lost: resume whenever you like."}
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <StatusPill run={investigation.run} label={current?.label} failed={investigation.status === "failed"} />
            {running ? (
              <Button kind="secondary" disabled={investigation.run === "stopping" || stop.isPending} onClick={() => stop.mutate()}>
                {investigation.run === "stopping" ? "Stopping…" : "Stop"}
              </Button>
            ) : investigation.status !== "failed" ? (
              <ResumeButton needsBudget={needsBudget} cap={cap} pending={resume.isPending} onResume={(b) => resume.mutate(b)} />
            ) : null}
          </div>
        </div>
        <ErrorNote error={stop.error ?? resume.error} />
        {stoppedEarly ? (
          <div role="status" className={cx("rounded-md px-4 py-3 body-s", lastRun.reason === "failed" || lastRun.reason === "account_refused" ? "bg-red-soft text-red" : "bg-amber-soft text-amber")}>
            {lastRun.message}
          </div>
        ) : null}
      </header>

      <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
        <Card className="flex flex-col gap-0 self-start">
          <MonoLabel>Stages</MonoLabel>
          <ol className="mt-2 flex flex-col">
            {(data?.rail ?? []).map((stage, n, all) => (
              <li key={stage.key} className="flex gap-3.5 pt-3.5">
                <span className="flex flex-col items-center gap-1">
                  <span aria-hidden className={cx(
                    "size-[18px] rounded-full",
                    stage.status === "done" && "bg-green",
                    stage.status === "active" && "bg-accent ring-4 ring-accent-soft",
                    stage.status === "paused" && "border-2 border-amber bg-amber-soft",
                    stage.status === "failed" && "bg-red",
                    (stage.status === "todo" || stage.status === "skipped") && "border-2 border-line bg-surface",
                  )} />
                  {n < all.length - 1 ? <span aria-hidden className={cx("h-6 w-0.5", stage.status === "done" ? "bg-green-soft" : "bg-line")} /> : null}
                </span>
                <span className="flex flex-col">
                  <span className={cx("heading-s", (stage.status === "todo" || stage.status === "skipped") && "text-ink-3")}>{stage.label}</span>
                  <span className="body-s text-ink-3">
                    {{ done: "Done", active: "Working", paused: "Paused here", failed: "Stopped here", todo: "To do", skipped: "Not needed" }[stage.status]}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </Card>

        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            {[
              ["Papers found", data?.counters.papers_found, "OpenAlex + arXiv"],
              ["Relevant", data?.counters.relevant, "scored 2–3"],
              ["Read closely", data?.counters.read, "verified quotes only"],
              ["Quotes verified", data?.counters.quotes_verified, "word for word"],
            ].map(([label, value, sub]) => (
              <Card key={label as string} className="flex flex-col gap-1.5 p-5">
                <MonoLabel>{label}</MonoLabel>
                <span className="display-l text-[44px]">{value ?? "–"}</span>
                <span className="body-s text-ink-3">{sub}</span>
              </Card>
            ))}
          </div>

          <Card className="flex flex-col gap-3 p-5">
            <div className="flex items-center justify-between">
              <MonoLabel>Cost so far</MonoLabel>
              <span className="heading-s">${cost.toFixed(2)} of ${cap.toFixed(2)} cap</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-pill bg-surface-muted" role="progressbar" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={cost}>
              <div className="h-full rounded-pill bg-accent transition-all" style={{ width: `${Math.min(100, (cost / cap) * 100)}%` }} />
            </div>
            <p className="body-s text-ink-3">
              Every paid step checks the cap first. If Anthropic refuses the account, the run pauses here instead of
              finishing with no evidence.
            </p>
          </Card>

          <Card className="flex flex-col gap-0 p-5">
            <div className="flex items-center justify-between pb-2">
              <MonoLabel>Activity</MonoLabel>
              {connected ? <span className="label-s text-green">● live</span> : null}
            </div>
            {activity.error ? <ErrorNote error={activity.error} /> : null}
            {feed.length === 0 ? <p className="body-s py-3 text-ink-3">Nothing yet.</p> : null}
            <ol aria-live="polite" className="flex max-h-[520px] flex-col overflow-y-auto">
              {feed.map((event) => (
                <li key={event.seq} className="flex flex-col gap-1.5 border-t border-line py-3 sm:flex-row sm:items-center sm:gap-3.5">
                  <span className="font-mono text-[11px] text-ink-3">{time(event.at)}</span>
                  <span className="w-24 shrink-0"><Pill tone={KIND_TONES[event.kind] ?? "neutral"}>{KIND_LABELS[event.kind] ?? event.kind}</Pill></span>
                  <span className="body-s flex-1">{event.message}</span>
                  {event.cost_usd !== null ? <span className="font-mono text-[11px] text-ink-3">${event.cost_usd.toFixed(2)}</span> : null}
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}

function StatusPill({ run, label, failed }: { run: Investigation["run"]; label?: string; failed: boolean }) {
  if (run === "running") return <Pill tone="spark">Running{label ? ` · ${label}` : ""}</Pill>;
  if (run === "stopping") return <Pill tone="amber">Stopping after this step</Pill>;
  if (failed) return <Pill tone="red">Failed</Pill>;
  return <Pill>Paused</Pill>;
}

function ResumeButton({ needsBudget, cap, pending, onResume }: {
  needsBudget: boolean; cap: number; pending: boolean; onResume: (budget?: number) => void;
}) {
  const [budget, setBudget] = useState(Math.min(20, cap + 3));
  if (!needsBudget) return <Button disabled={pending} onClick={() => onResume()}>{pending ? "Resuming…" : "Resume"}</Button>;
  return (
    <span className="flex items-center gap-2">
      <label className="body-s text-ink-2" htmlFor="new-budget">New budget $</label>
      <Input id="new-budget" type="number" min={cap + 0.5} max={20} step="0.5" value={budget}
        onChange={(e) => setBudget(Number(e.target.value))} className="w-24 py-2" />
      <Button disabled={pending || budget <= cap} onClick={() => onResume(budget)}>{pending ? "Resuming…" : "Resume"}</Button>
    </span>
  );
}
