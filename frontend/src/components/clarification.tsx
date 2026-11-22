/**
 * Screen 02 · Clarification (step 10b): Eureka's reading of the request, for the user to check, answer and confirm.
 *
 * Mode 1: the title, the research question and the idea's parts (key / proposed by Eureka / read from section D3 of
 * the user's document), each editable; the user's document and the claims the Skeptic will test. Mode 2: the
 * subtopics, any of which can be dropped (at least one stays). Both: up to 3 questions; a skipped one uses the
 * assumption shown. Confirming starts the evaluation (or discovery) in the background.
 */
"use client";

import { useState } from "react";

import { FacetGroups } from "@/components/facets";
import { Button, Card, ErrorNote, Input, MonoLabel, Pill, cx } from "@/components/ui";
import { useAnswerClarification, useClarification, type Clarification, type Investigation } from "@/lib/api/hooks";

export function ClarificationView({ investigation }: { investigation: Investigation }) {
  const clarification = useClarification(investigation.id);
  if (clarification.isPending) return <p className="body-s text-ink-3">Loading Eureka&apos;s reading…</p>;
  if (clarification.error) return <ErrorNote error={clarification.error} />;
  return <ClarificationForm investigation={investigation} reading={clarification.data} />;
}

/** What the confirmation sends: only facets whose text changed, answers in question order. */
export function changedFacets(original: { position: number; text: string }[], edited: Record<number, string>) {
  const out: Record<string, string> = {};
  for (const facet of original) {
    const text = edited[facet.position]?.trim();
    if (text && text !== facet.text) out[String(facet.position)] = text;
  }
  return out;
}

function ClarificationForm({ investigation, reading }: { investigation: Investigation; reading: Clarification }) {
  const answer = useAnswerClarification(investigation.id);
  const [answers, setAnswers] = useState<string[]>(reading.questions.map(() => ""));
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [dropped, setDropped] = useState<number[]>([]);
  const discover = reading.mode === "discover";
  const facets = reading.idea?.facets ?? [];
  const subtopics = reading.field_brief?.subtopics ?? [];

  const confirm = (skip: boolean) =>
    answer.mutate({
      answers: skip ? [] : answers.map((a) => a.trim() || null),
      edits: discover ? {} : changedFacets(facets, edits),
      remove_subtopics: discover ? dropped : [],
      confirm: true,
    });

  const proposed = facets.some((f) => f.is_key && !f.stated_by_user);
  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2.5">
        <MonoLabel>
          Investigations / {discover ? "Find me an idea" : "Evaluate my idea"} / <span className="text-ink">Step 1 of 3 · Confirm</span>
        </MonoLabel>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2.5">
            <h1 className="display-l">
              {discover ? <>Here&apos;s how Eureka maps <em>your field.</em></> : <>Here&apos;s how Eureka reads <em>your idea.</em></>}
            </h1>
            <p className="body-m max-w-2xl text-ink-2">
              {discover
                ? "These are the subtopics Eureka proposes to explore. Drop any that don't interest you, then confirm."
                : "Check the parts below. Anything marked “proposed” is Eureka's guess, so correct it before the evaluation starts."}
            </p>
          </div>
          {investigation.document ? <Pill tone="indigo">Read from your document</Pill> : null}
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
        <div className="flex flex-col gap-6">
          {discover ? (
            <Card className="flex flex-col gap-4">
              <MonoLabel>Subtopics to explore</MonoLabel>
              {subtopics.map((subtopic, n) => {
                const off = dropped.includes(n + 1);
                const lastOne = !off && subtopics.length - dropped.length <= 1;
                return (
                  <div key={subtopic.name} className={cx("flex items-start gap-4 border-t border-line pt-3.5", off && "opacity-50")}>
                    <span className="font-serif text-2xl italic text-accent">{n + 1}</span>
                    <div className="flex flex-1 flex-col gap-1">
                      <span className={cx("heading-s", off && "line-through")}>{subtopic.name}</span>
                      <span className="body-s text-ink-2">{subtopic.why_it_fits_you}</span>
                    </div>
                    <Button kind="ghost" className="px-3 py-1.5" disabled={lastOne}
                      onClick={() => setDropped(off ? dropped.filter((d) => d !== n + 1) : [...dropped, n + 1])}>
                      {off ? "Keep" : "Drop"}
                    </Button>
                  </div>
                );
              })}
            </Card>
          ) : reading.idea ? (
            <Card className="flex flex-col gap-5">
              <MonoLabel>Title</MonoLabel>
              <h2 className="heading-m -mt-3">{reading.idea.title}</h2>
              <MonoLabel>Research question</MonoLabel>
              <p className="-mt-3 rounded-md bg-accent-soft px-4 py-3.5 text-base leading-relaxed">{reading.idea.research_question}</p>
              <div className="flex items-center justify-between">
                <MonoLabel>The idea, in parts</MonoLabel>
                <button type="button" className="body-s font-medium text-indigo" onClick={() => setEditing(!editing)}>
                  {editing ? "Done editing" : "✎ Edit"}
                </button>
              </div>
              <div className="-mt-2">
                <FacetGroups facets={facets} edits={edits} editing={editing}
                  onEdit={(position, text) => setEdits({ ...edits, [position]: text })} />
              </div>
              {proposed ? (
                <p className="body-s text-amber">A key part was proposed by Eureka: check it, because the verdicts depend on it.</p>
              ) : null}
            </Card>
          ) : null}

          {reading.questions.length ? (
            <Card className="flex flex-col gap-5">
              <div>
                <h2 className="heading-m">
                  {reading.questions.length === 1 ? "1 question" : `${reading.questions.length} questions`} that would change the result
                </h2>
                <p className="body-s text-ink-3">Skip any: Eureka then uses the assumption shown.</p>
              </div>
              {reading.questions.map((q, n) => (
                <div key={q.question} className="flex flex-col gap-2">
                  <label className="flex gap-2.5" htmlFor={`answer-${n}`}>
                    <span className="font-serif text-2xl italic leading-6 text-accent">{n + 1}</span>
                    <span className="heading-s">{q.question}</span>
                  </label>
                  <Input id={`answer-${n}`} value={answers[n]} maxLength={1000} placeholder="Your answer…"
                    onChange={(e) => setAnswers(answers.map((a, i) => (i === n ? e.target.value : a)))} className="border-0 bg-paper" />
                  <span className="body-s text-ink-3">If skipped → {q.if_skipped}</span>
                </div>
              ))}
            </Card>
          ) : null}
        </div>

        <aside className="flex flex-col gap-6">
          {investigation.document ? (
            <Card className="flex flex-col gap-3.5 p-6">
              <MonoLabel>Your document</MonoLabel>
              <div className="flex items-center gap-3">
                <span className="rounded-sm bg-red-soft px-2.5 py-3 label-mono text-red">
                  {investigation.document.media_type.includes("pdf") ? "PDF" : "TXT"}
                </span>
                <div className="min-w-0">
                  <div className="heading-s truncate">{investigation.document.file_name}</div>
                  <div className="body-s text-ink-3">
                    {investigation.document.sections} sections · {investigation.document.verified_quotes} / {investigation.document.quotes} quotes verified
                  </div>
                </div>
              </div>
              <p className="rounded-md bg-green-soft px-3 py-2.5 body-s text-green">
                Private to this investigation. Never added to the shared library or sent to search engines.
              </p>
            </Card>
          ) : null}
          {reading.idea?.novelty_claims.length ? (
            <Card className="flex flex-col gap-3.5 p-6">
              <MonoLabel>Claims we&apos;ll test</MonoLabel>
              <p className="body-s text-ink-2">Your document says these are new. The Skeptic will try to disprove each one.</p>
              {reading.idea.novelty_claims.map((claim) => (
                <div key={claim.claim} className="flex flex-col gap-2 rounded-md bg-violet-soft px-3.5 py-3">
                  <span><Pill tone="violet">claim · {claim.section}</Pill></span>
                  <span className="body-s">“{claim.claim}”</span>
                </div>
              ))}
            </Card>
          ) : null}
          {reading.assumptions.length ? (
            <Card className="flex flex-col gap-2.5 p-6">
              <MonoLabel>Eureka assumes</MonoLabel>
              <ul className="flex list-disc flex-col gap-1.5 pl-4 body-s text-ink-2">
                {reading.assumptions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            </Card>
          ) : null}
          <Card dark className="flex flex-col gap-3.5 p-6">
            <MonoLabel className="text-accent">Next: {discover ? "discovery" : "full evaluation"}</MonoLabel>
            <p className="body-s text-ink-3">
              {discover ? "About $8–12 and 25–40 minutes." : "About $3–5 and 10–15 minutes."} Capped at $
              {investigation.budget_usd.toFixed(0)}: it stops before going over.
            </p>
            <ErrorNote error={answer.error} />
            <div className="flex flex-wrap gap-2.5">
              <Button kind="accent" disabled={answer.isPending} onClick={() => confirm(false)}>
                {answer.isPending ? "Starting…" : discover ? "Confirm & discover →" : "Confirm & evaluate →"}
              </Button>
              {reading.questions.length ? (
                <Button kind="ghost" className="text-paper hover:bg-white/10" disabled={answer.isPending} onClick={() => confirm(true)}>
                  Skip questions
                </Button>
              ) : null}
            </div>
          </Card>
        </aside>
      </div>
    </div>
  );
}
