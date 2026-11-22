/**
 * Live progress (step 10b): the investigation's event stream (SSE, GET /investigations/{id}/events) in the browser.
 *
 * - The page passes the stored events (`initial`); only events streamed since are kept in state, and the two are
 *   merged while rendering (mergeEvents: no repeats, ordered by sequence number, whatever order they arrive in).
 * - The stream asks only for events after the newest one the page has (?after=), so nothing arrives twice.
 * - The server ends the stream after the run's "ended" event (or at once if nothing runs). A browser's EventSource
 *   would then reconnect forever, so the stream is closed on "ended" and on any connection error, and reopened only
 *   while the page says a run is still working (`active`).
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { API } from "./api/client";
import type { ProgressEvent } from "./api/hooks";

export const EVENT_KINDS = ["stage", "step_started", "step_done", "candidate", "warning", "error", "ended"] as const;

export function mergeEvents(current: ProgressEvent[], incoming: ProgressEvent[]): ProgressEvent[] {
  if (incoming.length === 0) return current;
  const bySeq = new Map(current.map((event) => [event.seq, event]));
  for (const event of incoming) bySeq.set(event.seq, event);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

const newest = (events: ProgressEvent[] | undefined) => (events ?? []).reduce((max, e) => Math.max(max, e.seq), 0);

export function useLiveEvents(
  investigationId: string,
  { initial, active, onEvent }: { initial: ProgressEvent[] | undefined; active: boolean; onEvent?: (event: ProgressEvent) => void },
) {
  const [live, setLive] = useState<ProgressEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const known = useRef({ initial, live });
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    known.current = { initial, live };
    onEventRef.current = onEvent;
  });

  const loaded = initial !== undefined;
  useEffect(() => {
    if (!active || !loaded) return;
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const open = () => {
      const after = Math.max(newest(known.current.initial), newest(known.current.live));
      source = new EventSource(`${API}/investigations/${investigationId}/events?after=${after}`);
      source.onopen = () => setConnected(true);
      const receive = (message: Event) => {
        if (!(message instanceof MessageEvent) || !message.data) return; // a connection error, not a server event
        const event = JSON.parse(message.data) as ProgressEvent;
        setLive((current) => mergeEvents(current, [event]));
        onEventRef.current?.(event);
        if (event.kind === "ended") {
          stopped = true;
          source?.close();
          setConnected(false);
        }
      };
      for (const kind of EVENT_KINDS) source.addEventListener(kind, receive);
      source.onerror = (problem) => {
        // a server event named "error" (a step failed) arrives as an "error" too: that one is data, handled above
        if (problem instanceof MessageEvent && problem.data) return;
        // the server closed the stream, or the network dropped: never let the browser retry on its own
        source?.close();
        setConnected(false);
        if (!stopped) retry = setTimeout(open, 3_000);
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [investigationId, active, loaded]);

  const events = useMemo(() => mergeEvents(initial ?? [], live), [initial, live]);
  return { events, connected: active && connected };
}
