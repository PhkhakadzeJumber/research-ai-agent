/** The step pages' rules (Figma pages 3 and 4): where a run opens, when to move on by itself, how times read. */
import { describe, expect, it } from "vitest";

import { searchTable } from "@/components/step-outputs";

import { advanceTarget, durationText, landingStep, noticeText, stepHref, type StepSummary, type Steps } from "@/lib/steps";

const KEYS = ["idea", "search", "screening", "reading", "novelty", "skeptic", "value", "verdict", "package"];
const NAMES: Record<string, string> = { search: "Search", screening: "Screening", verdict: "Verdict" };
const run = (states: StepSummary["state"][], current: string | null): Steps => ({
  mode: "evaluate", run: current ? "running" : "idle", current,
  steps: KEYS.map((key, i) => ({ key, number: i + 1, label: NAMES[key] ?? key, name: NAMES[key] ?? key, state: states[i] ?? "upcoming", seconds: null, credits: null, optional: key === "package" })),
});

describe("where a run opens", () => {
  it("the step working or waiting, else the last finished one", () => {
    expect(landingStep(run(["done", "running"], "search"))).toBe("search");
    expect(landingStep(run(["waiting"], "idea"))).toBe("idea");
    expect(landingStep(run(Array(8).fill("done"), null))).toBe("verdict"); // not the optional package
  });
});

describe("moving on by itself", () => {
  const searching = run(["done", "running"], "search");
  const screening = run(["done", "done", "running"], "screening");
  it("when the step on screen finishes and the run goes on", () => {
    expect(advanceTarget(searching, screening, "search")).toBe("screening");
    expect(noticeText(screening, "search", "screening", 5)).toBe("✓ Search done · moving to Screening in 5 s");
  });
  it("never otherwise", () => {
    expect(advanceTarget(undefined, screening, "search")).toBeNull(); // opened after it finished
    expect(advanceTarget(searching, screening, "idea")).toBeNull(); // reading an older step
    expect(advanceTarget(searching, run(["done", "done"], null), "search")).toBeNull(); // the run ended or paused
    expect(advanceTarget(searching, run(["done", "paused"], "search"), "search")).toBeNull();
  });
});

describe("small texts and links", () => {
  it("durations, and finalists' own steps", () => {
    expect([durationText(45), durationText(100), durationText(3725), durationText(null)]).toEqual(["45 s", "1 min 40 s", "1 h 2 min", "not recorded"]);
    expect(stepHref("inv", "search")).toBe("/investigations/inv/step/search");
    expect(stepHref("inv", "verdict", "idea")).toBe("/investigations/inv/step/verdict?candidate=idea");
  });
});

describe("the searches table", () => {
  it("one row per phrasing, a column per source", () => {
    const q = (text: string, source: string, found: number, n: number) => ({ text, source, round: null, found, new: n });
    const { sources, rows } = searchTable([q("tokenizer tax", "openalex", 30, 30), q("tokenizer tax", "arxiv", 2, 0), q("agents", "openalex", 5, 4)]);
    expect(sources).toEqual(["arxiv", "openalex"]);
    expect(rows.map((r) => [r.text, r.bySource.openalex?.new, r.bySource.arxiv?.found])).toEqual([["tokenizer tax", 30, 2], ["agents", 4, undefined]]);
  });
});
