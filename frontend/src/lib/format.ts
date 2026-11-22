/**
 * How results are shown (step 10c), one place for the whole site:
 * - verdict colours, as in the Figma design: green = good, amber = has problems, red = blocks it, neutral = unclear.
 *   Novelty is never green (doc 12: a green "original" signal would overclaim); a supported opportunity is indigo.
 * - statement types of the research package: evidence green, interpretation indigo, hypothesis violet, to verify amber.
 * - money, dates, and an investigation's status in words.
 */
import type { Tone } from "@/components/ui";
import type { InvestigationSummary } from "@/lib/api/hooks";

const VERDICT_TONES: Record<string, Tone> = {
  // recommendation
  pursue: "green", refine: "amber", reconsider: "red",
  // novelty (never green)
  supported_opportunity: "indigo", potential_opportunity: "amber", incremental_variation: "neutral",
  previously_investigated: "red", insufficient_evidence: "neutral",
  // significance
  strong: "green", moderate: "amber", weak: "red",
  // feasibility
  feasible: "green", feasible_with_adjustments: "amber", not_feasible: "red",
  unclear: "neutral",
};

export const verdictTone = (category: string | null | undefined): Tone => VERDICT_TONES[category ?? ""] ?? "neutral";

export const STATEMENT_TONES: Record<string, Tone> = {
  evidence: "green", interpretation: "indigo", hypothesis: "violet", to_verify: "amber",
};

export const DIMENSION_LABELS: Record<string, string> = {
  novelty: "Novelty", significance: "Significance", feasibility: "Feasibility", recommendation: "Recommendation",
};

export const money = (usd: number | null | undefined) => `$${(usd ?? 0).toFixed(2)}`;

export function whenText(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const minutes = Math.round((now.getTime() - then.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60 && then.getDate() === now.getDate()) return "Today";
  return then.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** An investigation's state in a few words, with its pill colour (the list and headers). */
export function statusOf(i: Pick<InvestigationSummary, "run" | "status" | "stage" | "waiting_for">): [string, Tone] {
  if (i.run === "running") return [`Working · ${i.stage.replaceAll("_", " ")}`, "spark"];
  if (i.run === "stopping") return ["Stopping", "amber"];
  if (i.waiting_for === "clarification") return ["Waiting for your answers", "indigo"];
  if (i.status === "failed") return ["Failed", "red"];
  if (i.stage === "results_ready" || i.stage === "writing_proposal") return ["Results ready", "green"];
  if (i.status === "created") return ["Not started", "neutral"];
  return ["Paused · resume", "amber"];
}

export const KIND_LABELS: Record<string, string> = {
  problem: "Problem", method: "Method", data_setting: "Data / setting", evaluation: "Evaluation",
  contribution: "Contribution",
};

export const words = (code: string | null | undefined) => (code ?? "").replaceAll("_", " ");
