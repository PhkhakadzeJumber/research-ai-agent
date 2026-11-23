/**
 * The account's plan in words (step P4; docs/18): what it may start, at what price in credits, and how to say so.
 * The server decides (src/api/billing.py); these only explain it before the user clicks, so a click that would be
 * refused says why instead.
 */
import type { Me } from "@/lib/api/hooks";

export type Action = "evaluate" | "discover" | "package";
export type Plan = Me["plan"];

export type Check = { ok: true } | { ok: false; reason: string; upgrade: boolean };

/** Whether the account can start this action now, and if not, why (and whether Pro or a top-up would help). */
export function canStart(plan: Plan | undefined, action: Action, emailVerified = true): Check {
  if (!plan) return { ok: true }; // not loaded yet: the server will say
  if (plan.plan === "free") {
    if (action === "evaluate" && !emailVerified) return { ok: false, reason: "Verify your email first: open the link we sent you (the banner at the top can send a new one).", upgrade: false };
    if (action !== "evaluate") return { ok: false, reason: `${action === "discover" ? "Finding ideas" : "Research packages"} are part of Pro.`, upgrade: true };
    if ((plan.free_evaluations_left ?? 0) <= 0) return { ok: false, reason: "You've used this month's free evaluation. It comes back on the 1st.", upgrade: true };
    return { ok: true };
  }
  const price = plan.prices[action];
  if (plan.credits.available < price) {
    return { ok: false, reason: `This needs ${price} credits; you have ${plan.credits.available}.`, upgrade: true };
  }
  return { ok: true };
}

/** "25 credits", or the free plan's "Free · 1 left this month". */
export function priceText(plan: Plan | undefined, action: Action): string {
  if (!plan) return "";
  if (plan.plan === "free" && action === "evaluate") {
    const left = plan.free_evaluations_left ?? 0;
    return left > 0 ? `Free · ${left} left this month` : "Free evaluation used";
  }
  return `${plan.plan === "free" ? "Pro · " : ""}${plan.prices[action]} credits`;
}

/** The nav chip: [label, value]. */
export function chipText(plan: Plan): [string, string] {
  if (plan.plan === "pro") return ["Pro", `${plan.credits.available} credit${plan.credits.available === 1 ? "" : "s"}`];
  const left = plan.free_evaluations_left ?? 0;
  return ["Free", left > 0 ? `${left} evaluation${left === 1 ? "" : "s"} left` : "Upgrade"];
}

/** An investigation's credits in words: "9 of 25 credits", "free plan", "22 credits". */
export function creditsText(credits: { used: number; limit: number; free: boolean }, running: boolean): string {
  if (credits.free) return "Free plan";
  if (running && credits.limit > credits.used) return `${credits.used} of ${credits.limit} credits`;
  return `${credits.used} credit${credits.used === 1 ? "" : "s"}`;
}

/** Why a run stopped, for the user (the stored message speaks dollars and providers: it is for admins). */
export function endedText(reason: string, message: string): string {
  if (reason === "budget") return "This run used what its price allows and paused. Resume to finish it: it continues with what is left of the price, or at most a fifth of it if that is used up.";
  if (reason === "account_refused") return "Our AI provider didn't accept the request just now (on our side, not yours). Nothing is charged for it: resume in a little while.";
  if (reason === "failed") return "Something went wrong on our side. Nothing is charged for it, and nothing is lost: resume to try again.";
  return message;
}
