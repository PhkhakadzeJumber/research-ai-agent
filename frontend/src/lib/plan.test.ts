/** Step P4: what the plan allows, said before the click (the server decides: src/api/billing.py). */
import { describe, expect, it } from "vitest";

import { canStart, chipText, creditsText, endedText, priceText, type Plan } from "@/lib/plan";

const prices = { evaluate: 25, discover: 60, package: 5 };
const credits = (available: number) => ({ available, monthly: available, other: 0, held: 0 });
const free = (left: number): Plan => ({ plan: "free", plan_until: null, credits: credits(0), free_evaluations_left: left, prices });
const pro = (available: number): Plan => ({ plan: "pro", plan_until: "2027-11-22T00:00:00+04:00", credits: credits(available), free_evaluations_left: null, prices });

describe("what a plan may start", () => {
  it("free: one evaluation a month, nothing else", () => {
    expect(canStart(free(1), "evaluate")).toEqual({ ok: true });
    expect(canStart(free(0), "evaluate")).toMatchObject({ ok: false, upgrade: true });
    expect(canStart(free(1), "discover")).toEqual({ ok: false, reason: "Finding ideas are part of Pro.", upgrade: true });
    expect(canStart(free(1), "package")).toMatchObject({ ok: false, reason: "Research packages are part of Pro." });
  });
  it("pro: anything the credits cover", () => {
    expect(canStart(pro(60), "discover")).toEqual({ ok: true });
    expect(canStart(pro(24), "evaluate")).toEqual({ ok: false, reason: "This needs 25 credits; you have 24.", upgrade: true });
  });
  it("lets the server decide while the plan is loading", () => {
    expect(canStart(undefined, "discover")).toEqual({ ok: true });
  });
});

describe("prices and the chip in words", () => {
  it("shows credits, or the free evaluation", () => {
    expect(priceText(pro(100), "evaluate")).toBe("25 credits");
    expect(priceText(free(1), "evaluate")).toBe("Free · 1 left this month");
    expect(priceText(free(0), "evaluate")).toBe("Free evaluation used");
    expect(priceText(free(1), "discover")).toBe("Pro · 60 credits");
    expect(chipText(pro(1))).toEqual(["Pro", "1 credit"]);
    expect(chipText(free(1))).toEqual(["Free", "1 evaluation left"]);
    expect(chipText(free(0))).toEqual(["Free", "Upgrade"]);
  });
  it("an investigation's credits: of the reserved price while running, then what it used", () => {
    expect(creditsText({ used: 9, limit: 25, free: false }, true)).toBe("9 of 25 credits");
    expect(creditsText({ used: 22, limit: 22, free: false }, false)).toBe("22 credits");
    expect(creditsText({ used: 0, limit: 25, free: true }, true)).toBe("Free plan");
  });
  it("never shows a user dollars or the provider's name when a run stops", () => {
    for (const reason of ["budget", "account_refused", "failed"]) {
      expect(endedText(reason, "Budget used up: $1.51 of $1.5. Anthropic refused")).not.toMatch(/\$|Anthropic/);
    }
    expect(endedText("stopped", "Stopped at screening.")).toBe("Stopped at screening.");
  });
});
