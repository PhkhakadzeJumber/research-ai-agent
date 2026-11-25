/**
 * The step pages' small rules (Figma pages 3 and 4), kept out of the components so they can be tested: links, how
 * long a step took, where a run opens, and when to move on by itself.
 */
import type { Schemas } from "@/lib/api/client";

export type Steps = Schemas["Steps"];
export type StepSummary = Schemas["StepSummary"];
export type StepPage = Schemas["StepPage"];

/** The page of one step; a Mode 2 finalist's own steps carry its id. */
export function stepHref(investigationId: string, key: string, candidate?: string | null): string {
  return `/investigations/${investigationId}/step/${key}${candidate ? `?candidate=${candidate}` : ""}`;
}

/** 100 → "1 min 40 s"; null → "not recorded" (runs from before step times were kept). */
export function durationText(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "not recorded";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, "0")} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** Where an investigation opens: the step in progress, else the last finished one (the verdict, the ranking). */
export function landingStep(steps: Steps): string {
  if (steps.current) return steps.current;
  const done = steps.steps.filter((s) => s.state === "done" && !s.optional);
  return (done[done.length - 1] ?? steps.steps[0]).key;
}

/**
 * Auto-advance: the step on screen was running and has just finished while the run went on to another step: move
 * there (after the 5 s notice, unless the user chose to stay). `following`: the page was reached by an automatic
 * move, so it keeps following the run even if its step finished before the page saw it run (a quick step).
 * Nothing to do when the run stopped, waits for the user, or ended (a finished run stays on the page you're reading).
 */
export function advanceTarget(before: Steps | undefined, after: Steps, viewing: string, following = false): string | null {
  const was = before?.steps.find((s) => s.key === viewing)?.state;
  const now = after.steps.find((s) => s.key === viewing)?.state;
  if (now !== "done" || !after.current || after.current === viewing || after.run === "idle") return null;
  return was === "running" || following ? after.current : null;
}

/** Where an automatic move goes: the step, marked as followed (the next pages keep following the run). */
export function followHref(investigationId: string, key: string): string {
  return `${stepHref(investigationId, key)}?follow=1`;
}

/** The step's name for the notice: "✓ Search done · moving to Screening in 5 s". */
export function noticeText(steps: Steps, from: string, to: string, seconds: number): string {
  const name = (key: string) => steps.steps.find((s) => s.key === key)?.name ?? key;
  return `✓ ${name(from)} done · moving to ${name(to)} in ${seconds} s`;
}

/** Mode 2's step 7, per finalist: the steps of an evaluation (Mode 1's search … verdict). */
export const FINALIST_STEPS: [string, string][] = [
  ["search", "Search"], ["screening", "Screening"], ["reading", "Reading"], ["novelty", "Novelty"],
  ["skeptic", "Skeptic"], ["value", "Value & feasibility"], ["verdict", "Verdict"],
];
