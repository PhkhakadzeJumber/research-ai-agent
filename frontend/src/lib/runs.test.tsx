/** Step 10b: starting a run, the clarification and the live progress stream. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { changedFacets } from "@/components/clarification";
import { checkFile, parseSeeds, typedConstraints } from "@/components/start";
import type { ProgressEvent } from "@/lib/api/hooks";
import { mergeEvents, useLiveEvents } from "@/lib/live";

const event = (seq: number, kind = "step_done", message = `event ${seq}`): ProgressEvent => ({
  seq, kind, message, stage: null, step: null, data: {}, candidate_id: null, cost_usd: 0.5, at: "2026-11-22T17:00:00+04:00",
});

describe("starting a run", () => {
  it("sends only the limits changed for this run", () => {
    const profile = { weeks_available: 12, compute: "free_notebook_gpu" as const };
    expect(typedConstraints(profile, { weeks: 12, compute: "free_notebook_gpu" })).toEqual({});
    expect(typedConstraints(profile, { weeks: 8, compute: "one_gpu" })).toEqual({
      "available time": "8 weeks", compute: "one GPU for days",
    });
    expect(typedConstraints(undefined, { weeks: null, compute: null })).toEqual({});
  });

  it("reads seed papers from lines or commas", () => {
    expect(parseSeeds("2401.12345, 10.1/x\n\n2401.12345\nSome title")).toEqual(["2401.12345", "10.1/x", "Some title"]);
    expect(parseSeeds(Array.from({ length: 12 }, (_, n) => `p${n}`).join(","))).toHaveLength(10);
  });

  it("accepts only .pdf, .md and .txt up to 10 MB", () => {
    expect(checkFile(new File(["x"], "draft.PDF"))).toBeNull();
    expect(checkFile(new File(["x"], "draft.docx"))).toMatch(/Only/);
    const big = new File(["x"], "big.txt");
    Object.defineProperty(big, "size", { value: 10 * 1024 * 1024 + 1 });
    expect(checkFile(big)).toMatch(/10 MB/);
  });
});

describe("the clarification", () => {
  it("sends only the parts whose text changed", () => {
    const facets = [{ position: 0, text: "problem" }, { position: 1, text: "method" }];
    expect(changedFacets(facets, { 0: "problem", 1: " RAG with BM25 " })).toEqual({ "1": "RAG with BM25" });
    expect(changedFacets(facets, { 1: "   " })).toEqual({});
  });
});

describe("live events", () => {
  it("merges without repeats, in order", () => {
    expect(mergeEvents([event(1), event(3)], [event(2), event(3, "ended")]).map((e) => [e.seq, e.kind])).toEqual([
      [1, "step_done"], [2, "step_done"], [3, "ended"],
    ]);
  });

  class FakeSource {
    static all: FakeSource[] = [];
    listeners: Record<string, ((e: Event) => void)[]> = {};
    onopen: (() => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    closed = false;
    constructor(public url: string) { FakeSource.all.push(this); }
    addEventListener(kind: string, fn: (e: Event) => void) { (this.listeners[kind] ??= []).push(fn); }
    close() { this.closed = true; }
    send(e: ProgressEvent) {
      const message = new MessageEvent(e.kind, { data: JSON.stringify(e) });
      for (const fn of this.listeners[e.kind] ?? []) fn(message);
      if (e.kind === "error") this.onerror?.(message); // a server "error" event reaches onerror too, as in browsers
    }
  }

  beforeEach(() => {
    FakeSource.all = [];
    vi.stubGlobal("EventSource", FakeSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("asks for what is new, adds live events, and stops at the run's end", () => {
    const seen: number[] = [];
    const initial = [event(1), event(2)];
    const { result } = renderHook(() => useLiveEvents("inv", { initial, active: true, onEvent: (e) => seen.push(e.seq) }));
    const [source] = FakeSource.all;
    expect(source.url).toBe("/api/v1/investigations/inv/events?after=2");

    act(() => source.send(event(3, "error", "Generating ideas failed")));
    expect(source.closed).toBe(false); // a step that failed is news, not a broken connection
    act(() => source.send(event(4, "ended")));
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    expect(seen).toEqual([3, 4]);
    expect(source.closed).toBe(true);
    act(() => vi.advanceTimersByTime(10_000));
    expect(FakeSource.all).toHaveLength(1); // the run ended: no reconnecting
  });

  it("reconnects after a dropped connection, asking for what it missed", () => {
    renderHook(() => useLiveEvents("inv", { initial: [event(1)], active: true }));
    const [first] = FakeSource.all;
    act(() => first.send(event(2)));
    act(() => first.onerror?.(new Event("error")));
    expect(first.closed).toBe(true);
    act(() => vi.advanceTimersByTime(3_000));
    expect(FakeSource.all).toHaveLength(2);
    expect(FakeSource.all[1].url).toBe("/api/v1/investigations/inv/events?after=2");
  });

  it("doesn't connect while nothing runs", () => {
    renderHook(() => useLiveEvents("inv", { initial: [event(1)], active: false }));
    expect(FakeSource.all).toHaveLength(0);
  });
});
