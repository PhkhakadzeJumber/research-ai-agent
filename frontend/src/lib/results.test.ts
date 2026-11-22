/** Step 10c: how results are shown. */
import { describe, expect, it } from "vitest";

import { traceText } from "@/components/idea";
import { shortTitle } from "@/components/package";
import { ownIdea } from "@/components/results";
import type { Results } from "@/lib/api/hooks";
import { statusOf, verdictTone, whenText } from "@/lib/format";

describe("verdict colours", () => {
  it("never shows novelty in green (doc 12: no 'original' signal)", () => {
    for (const novelty of ["supported_opportunity", "potential_opportunity", "incremental_variation", "previously_investigated", "insufficient_evidence"]) {
      expect(verdictTone(novelty)).not.toBe("green");
    }
  });
  it("green is good, amber has problems, red blocks", () => {
    expect([verdictTone("pursue"), verdictTone("refine"), verdictTone("reconsider")]).toEqual(["green", "amber", "red"]);
    expect([verdictTone("feasible"), verdictTone("feasible_with_adjustments"), verdictTone("not_feasible")]).toEqual(["green", "amber", "red"]);
    expect(verdictTone("something new")).toBe("neutral");
  });
});

describe("an investigation's state in words", () => {
  const base = { run: "idle", status: "running", stage: "full_evaluation", waiting_for: null } as const;
  it("says what the user can do", () => {
    expect(statusOf({ ...base, run: "running" })).toEqual(["Working · full evaluation", "spark"]);
    expect(statusOf({ ...base, stage: "awaiting_clarification", waiting_for: "clarification" })[0]).toBe("Waiting for your answers");
    expect(statusOf({ ...base, status: "completed", stage: "results_ready" })).toEqual(["Results ready", "green"]);
    expect(statusOf({ ...base, status: "failed" })).toEqual(["Failed", "red"]);
    expect(statusOf(base)).toEqual(["Paused · resume", "amber"]);
  });
});

describe("small texts", () => {
  it("dates read like people say them", () => {
    const now = new Date("2026-11-22T18:00:00+04:00");
    expect(whenText("2026-11-22T17:58:00+04:00", now)).toBe("2 min ago");
    expect(whenText("2026-11-22T09:00:00+04:00", now)).toBe("Today");
    expect(whenText("2026-11-20T09:00:00+04:00", now)).toBe("20 Nov");
  });
  it("rule traces lose their rule code", () => {
    expect(traceText("N4: no verified 'same' on the key facets")).toBe("No verified 'same' on the key facets");
    expect(traceText("R2: every dimension is good enough")).toBe("Every dimension is good enough");
    expect(traceText("N1S: override")).toBe("Override");
  });
  it("long titles are cut at a word", () => {
    expect(shortTitle("Token tax in agent trajectories: does tokenizer fertility predict the gap?")).toBe("Token tax in agent trajectories: does tokenizer…");
    expect(shortTitle("Short title")).toBe("Short title");
  });
});

describe("a Mode 1 result", () => {
  it("finds the user's own idea wherever triage put it", () => {
    const idea = (id: string, origin: string, version: number) =>
      ({ id, origin, version, title: id, research_question: "", status: "rejected" });
    const results = {
      finalists: [{ candidate: idea("alternative", "alternatives", 1) }],
      advanced: [],
      others: [{ candidate: idea("v2", "user", 2), why: "" }, { candidate: idea("mine", "user", 1), why: "" }],
    } as unknown as Results;
    expect(ownIdea(results)?.id).toBe("mine");
  });
});

describe("the idea, in parts", () => {
  it("groups by kind in the design's order, key parts first", async () => {
    const { groupFacets } = await import("@/components/facets");
    const facet = (position: number, kind: string, is_key = false) => ({ position, kind, text: `${kind} ${position}`, is_key });
    const groups = groupFacets([facet(0, "method"), facet(1, "problem"), facet(2, "method", true), facet(3, "contribution"), facet(4, "problem")]);
    expect(groups.map(([kind, parts]) => [kind, parts.map((p) => p.position)])).toEqual([
      ["problem", [1, 4]], ["method", [2, 0]], ["contribution", [3]],
    ]);
  });
});
