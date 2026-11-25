/**
 * A run, one step per page (Figma pages 3 and 4): /investigations/{id}/step/{key}, and for a Mode 2 finalist's own
 * evaluation ?candidate={id} with the steps search … verdict.
 *
 * Every page: the header (title, status, credits, Stop / Resume), the stepper (done steps open; the one on screen is
 * underlined), then "What this step does", "What Eureka did" and the step's output, with its numbers and its own
 * log on the right, and Previous / Next. While a run works the page refreshes every 3 s; when the step on screen
 * finishes and the run moves on, a notice counts 5 s and opens the next step, unless the user chooses "Stay here"
 * (the next pages keep filling in; the stepper opens them any time).
 */
"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ClarificationView } from "@/components/clarification";
import { StepOutputView } from "@/components/step-outputs";
import { Button, ButtonLink, Card, ErrorNote, MonoLabel, Pill, cx } from "@/components/ui";
import { useIdea, useInvestigation, useResume, useStepPage, useSteps, useStop, type Investigation } from "@/lib/api/hooks";
import { statusOf } from "@/lib/format";
import { creditsText } from "@/lib/plan";
import { FINALIST_STEPS, advanceTarget, noticeText, stepHref, type StepSummary, type Steps } from "@/lib/steps";

const ADVANCE_SECONDS = 5;

export function StepScreen() {
  const { id, key } = useParams<{ id: string; key: string }>();
  const candidate = useSearchParams().get("candidate");
  const investigation = useInvestigation(id);
  const live = Boolean(investigation.data && investigation.data.run !== "idle");
  const steps = useSteps(id, live);
  const page = useStepPage(id, key, candidate, live);

  if (investigation.isPending || steps.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  if (investigation.error || steps.error) {
    return (
      <div className="flex max-w-xl flex-col gap-4">
        <ErrorNote error={investigation.error ?? steps.error} />
        <Link href="/investigations" className="body-s text-indigo">← Your investigations</Link>
      </div>
    );
  }
  return <StepLayout investigation={investigation.data} steps={steps.data} viewing={key} candidate={candidate} page={page} />;
}

function StepLayout({ investigation, steps, viewing, candidate, page }: {
  investigation: Investigation; steps: Steps; viewing: string; candidate: string | null; page: ReturnType<typeof useStepPage>;
}) {
  const evaluate = investigation.mode === "evaluate";
  const finalist = useIdea(candidate ?? "");
  const current = steps.steps.find((s) => s.key === (candidate ? "full" : viewing));
  const waiting = !candidate && (viewing === "idea" || viewing === "field") && investigation.waiting_for === "clarification";
  const [status, tone] = statusOf(investigation);
  const notice = useAutoAdvance(investigation.id, steps, viewing, Boolean(candidate));

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <MonoLabel>
          <Link href="/investigations" className="hover:text-ink">Investigations</Link> / {evaluate ? "Evaluate my idea" : "Find me an idea"} /{" "}
          <span className="text-ink">
            {candidate ? `Full evaluation · ${finalist.data?.title ?? "finalist"}` : `Step ${current?.number ?? "?"} of ${steps.steps.length}`}
          </span>
        </MonoLabel>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="display-l mr-auto">{investigation.title}</h1>
          <Pill tone={tone}>{status}</Pill>
          {investigation.credits.used || investigation.credits.limit || investigation.credits.free ? (
            <span className="body-s text-ink-3">{creditsText(investigation.credits, investigation.run !== "idle")}</span>
          ) : null}
          <RunControls investigation={investigation} />
        </div>
      </header>

      <Stepper steps={steps.steps} viewing={candidate ? "full" : viewing} investigationId={investigation.id} />
      {candidate ? <FinalistStepper investigationId={investigation.id} candidate={candidate} viewing={viewing} /> : null}

      {waiting ? <ClarificationView investigation={investigation} /> : page.isPending ? (
        <p className="body-s text-ink-3">Loading the step…</p>
      ) : page.error ? <ErrorNote error={page.error} /> : (
        <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
          <div className="flex min-w-0 flex-col gap-5">
            <Card tone="muted" className="flex flex-col gap-1.5 p-5">
              <MonoLabel>{`Step ${page.data.step.number} · ${page.data.step.name} · what this step does`}</MonoLabel>
              <p className="body-m text-ink-2">{page.data.does}</p>
            </Card>
            <Card className="flex flex-col gap-2.5 p-6">
              <h2 className="heading-m">{page.data.step.state === "running" ? "What Eureka is doing" : "What Eureka did"}</h2>
              <p className="body-m">{page.data.did}</p>
            </Card>
            <StepOutputView page={page.data} investigation={investigation} />
          </div>
          <aside className="flex flex-col gap-5">
            <Card className="flex flex-col gap-2.5 p-6">
              <MonoLabel>This step</MonoLabel>
              <Row label="State" value={stateText(page.data.step)} />
              {page.data.stats.map((s) => <Row key={s.label} label={s.label} value={s.value} />)}
            </Card>
            {page.data.log.length ? (
              <Card className="flex flex-col gap-0 p-6">
                <div className="flex justify-between pb-2">
                  <MonoLabel>Log of this step</MonoLabel>
                  {page.data.step.state === "running" ? <span className="label-s text-green">● live</span> : null}
                </div>
                {page.data.log.map((line, n) => (
                  <div key={n} className="flex gap-3 border-t border-line py-2.5">
                    <span className="font-mono text-[11px] text-ink-3">{new Date(line.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span>
                    <span className="body-s">{line.message}</span>
                  </div>
                ))}
              </Card>
            ) : null}
          </aside>
        </div>
      )}

      {page.data && !waiting ? (
        <nav aria-label="Steps" className="flex flex-wrap justify-between gap-3">
          {page.data.previous ? (
            <ButtonLink href={stepHref(investigation.id, page.data.previous, candidate)} kind="secondary">← {nameOf(steps, page.data.previous)}</ButtonLink>
          ) : candidate ? <ButtonLink href={stepHref(investigation.id, "full")} kind="secondary">← All finalists</ButtonLink> : <span />}
          {page.data.next ? <NextLink steps={steps} next={page.data.next} href={stepHref(investigation.id, page.data.next, candidate)} finalist={Boolean(candidate)} /> : null}
        </nav>
      ) : null}

      {notice ? (
        <div role="status" className="fixed inset-x-0 bottom-6 z-30 mx-auto flex w-fit items-center gap-3.5 rounded-pill bg-ink py-3 pl-5 pr-3 text-paper shadow-lg">
          <span className="label-s">{notice.text}</span>
          <button type="button" onClick={notice.stay} className="rounded-pill bg-white/15 px-3.5 py-1.5 label-s hover:bg-white/25">Stay here</button>
        </div>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-3 body-s"><span className="text-ink-2">{label}</span><span className="heading-s text-right">{value}</span></div>;
}

function stateText(step: StepSummary): string {
  return { done: "Done", running: "Running", waiting: "Waiting for you", paused: "Paused", failed: "Failed", upcoming: step.optional ? "Optional" : "Not reached" }[step.state];
}

function nameOf(steps: Steps, key: string): string {
  return steps.steps.find((s) => s.key === key)?.name ?? FINALIST_STEPS.find(([k]) => k === key)?.[1] ?? key;
}

function NextLink({ steps, next, href, finalist }: { steps: Steps; next: string; href: string; finalist: boolean }) {
  const state = finalist ? "done" : steps.steps.find((s) => s.key === next)?.state;
  if (state === "upcoming" && !steps.steps.find((s) => s.key === next)?.optional) {
    return <span className="rounded-pill bg-ink/30 px-5 py-2.5 label-s text-paper" aria-disabled>{nameOf(steps, next)} (when ready)</span>;
  }
  return <ButtonLink href={href} kind="primary">{nameOf(steps, next)}{state === "running" ? " (running)" : ""} →</ButtonLink>;
}

const MARK: Record<StepSummary["state"], [string, string]> = {
  done: ["✓", "border-green bg-green-soft text-green"],
  running: ["●", "border-accent bg-accent text-ink"],
  waiting: ["●", "border-indigo bg-indigo-soft text-indigo"],
  paused: ["Ⅱ", "border-amber bg-amber-soft text-amber"],
  failed: ["!", "border-red bg-red-soft text-red"],
  upcoming: ["", "border-line bg-surface text-ink-3"],
};

/** The steps of the mode, in order; finished and current steps open their page. */
export function Stepper({ steps, viewing, investigationId }: { steps: StepSummary[]; viewing: string; investigationId: string }) {
  return (
    <nav aria-label="Run steps" className="flex flex-wrap items-center gap-x-1 gap-y-2 rounded-lg border border-line bg-surface p-2.5">
      {steps.map((s, n) => {
        const [mark, ring] = MARK[s.state];
        const open = s.state !== "upcoming" || s.optional;
        const body = (
          <span className={cx("flex items-center gap-1.5 rounded-pill px-2 py-1.5", s.state === "running" && "bg-accent-soft", s.key === viewing && "underline decoration-2 underline-offset-[6px]")}>
            <span className={cx("grid size-[24px] place-items-center rounded-full border text-[12px]", ring)}>{mark || s.number}</span>
            <span className={cx("label-s", s.state === "upcoming" ? "text-ink-3" : "text-ink", s.key === viewing && "font-semibold")}>{s.label}</span>
          </span>
        );
        return (
          <span key={s.key} className="flex items-center gap-1">
            {open ? <Link href={stepHref(investigationId, s.key)} aria-current={s.key === viewing ? "step" : undefined}>{body}</Link> : body}
            {n < steps.length - 1 ? <span aria-hidden className="h-px w-2 bg-line" /> : null}
          </span>
        );
      })}
    </nav>
  );
}

function FinalistStepper({ investigationId, candidate, viewing }: { investigationId: string; candidate: string; viewing: string }) {
  return (
    <nav aria-label="Finalist steps" className="flex flex-wrap items-center gap-2 rounded-lg bg-paper p-2">
      <MonoLabel className="px-2">Its evaluation:</MonoLabel>
      {FINALIST_STEPS.map(([key, label]) => (
        <Link key={key} href={stepHref(investigationId, key, candidate)} aria-current={key === viewing ? "step" : undefined}
          className={cx("rounded-pill px-3 py-1.5 label-s", key === viewing ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}>
          {label}
        </Link>
      ))}
    </nav>
  );
}

function RunControls({ investigation }: { investigation: Investigation }) {
  const stop = useStop(investigation.id);
  const resume = useResume(investigation.id);
  if (investigation.run === "running" || investigation.run === "stopping") {
    return <Button kind="secondary" disabled={investigation.run === "stopping" || stop.isPending} onClick={() => stop.mutate()}>{investigation.run === "stopping" ? "Stopping…" : "Stop"}</Button>;
  }
  const finished = ["results_ready", "writing_proposal"].includes(investigation.stage);
  if (finished || investigation.waiting_for || investigation.status === "failed") return null;
  return (
    <span className="flex flex-col items-end gap-1">
      <Button disabled={resume.isPending} onClick={() => resume.mutate(undefined)}>{resume.isPending ? "Resuming…" : "Resume"}</Button>
      <ErrorNote error={resume.error} />
    </span>
  );
}

/** When the step on screen finishes and the run moves on: count 5 s, then open the step that's working now. */
function useAutoAdvance(investigationId: string, steps: Steps, viewing: string, finalist: boolean) {
  const router = useRouter();
  const before = useRef<Steps | undefined>(undefined);
  const [advance, setAdvance] = useState<{ to: string; text: string; left: number } | null>(null);

  useEffect(() => {
    const to = finalist ? null : advanceTarget(before.current, steps, viewing);
    before.current = steps;
    if (!to) return;
    const start = setTimeout(() => setAdvance({ to, text: noticeText(steps, viewing, to, ADVANCE_SECONDS), left: ADVANCE_SECONDS }), 0);
    return () => clearTimeout(start);
  }, [steps, viewing, finalist]);

  useEffect(() => {
    if (!advance) return;
    if (advance.left <= 0) {
      router.push(stepHref(investigationId, advance.to));
      return;
    }
    const tick = setTimeout(() => setAdvance((a) => (a ? { ...a, left: a.left - 1, text: a.text.replace(/in \d+ s$/, `in ${a.left - 1} s`) } : a)), 1000);
    return () => clearTimeout(tick);
  }, [advance, investigationId, router]);

  return advance ? { text: advance.text, stay: () => setAdvance(null) } : null;
}
