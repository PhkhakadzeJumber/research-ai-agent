/**
 * Screen 01 · Start (step 10b): the two ways in, and the limits for this run.
 *
 * - Mode 1 (Evaluate my idea): the idea as text, or the user's own document (.pdf / .md / .txt, sent to Claude only
 *   with their consent), plus papers they already know (seeds: DOIs, arXiv IDs or titles).
 * - Mode 2 (Find me an idea): field, interests, what to avoid, how many ideas.
 * - The limits bar: complexity, available weeks and compute, and the plan. Weeks and compute start from the
 *   profile; only what is changed here is sent, and it overrides the profile for this run (the backend merges the two).
 * - Prices are in credits (step P4; docs/18): the plan sets what a run may cost, so there is no budget to type. A
 *   free account sees Mode 2 locked and its free evaluation; a click the plan wouldn't allow says why first.
 */
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";

import { PlanNote } from "@/components/plan-note";
import { Button, ButtonLink, Card, ErrorNote, Field, Input, MonoLabel, Pill, Segmented, TextArea, cx } from "@/components/ui";
import { useCreateFromDocument, useCreateInvestigation, useMe, useProfile, type ResearchLimits } from "@/lib/api/hooks";
import { canStart, chipText, priceText, type Plan } from "@/lib/plan";

const MAX_IDEA = 2000;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const FILE_TYPES = [".pdf", ".md", ".txt"];

type Complexity = "beginner" | "intermediate" | "advanced";
type Compute = NonNullable<ResearchLimits["compute"]>;

const COMPUTE_LABELS: Record<Compute, string> = {
  laptop: "laptop only",
  free_notebook_gpu: "free notebook GPU (e.g. Colab)",
  one_gpu: "one GPU for days",
  cluster: "a cluster",
};

export type RunLimits = { weeks: number | null; compute: Compute | null };

/** The limits typed for this run that differ from the profile, as the readable pairs investigations store. */
export function typedConstraints(profile: ResearchLimits | undefined, run: RunLimits): Record<string, string> {
  const out: Record<string, string> = {};
  if (run.weeks && run.weeks !== (profile?.weeks_available ?? null)) out["available time"] = `${run.weeks} weeks`;
  if (run.compute && run.compute !== (profile?.compute ?? null)) out["compute"] = COMPUTE_LABELS[run.compute];
  return out;
}

/** Seeds as typed (one per line or comma-separated), without blanks or repeats, at most 10. */
export function parseSeeds(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean))].slice(0, 10);
}

export function checkFile(file: File): string | null {
  const ok = FILE_TYPES.some((ext) => file.name.toLowerCase().endsWith(ext));
  if (!ok) return "Only .pdf, .md and .txt files are supported.";
  if (file.size > MAX_FILE_BYTES) return "The file is over 10 MB.";
  return null;
}

export function StartScreen() {
  const profile = useProfile();
  if (profile.isPending) return <p className="body-s text-ink-3">Loading…</p>;
  // start from the profile's limits once (a key remounts the form if the profile changes)
  return <Start key={profile.dataUpdatedAt} limits={profile.data?.limits} />;
}

function Start({ limits }: { limits: ResearchLimits | undefined }) {
  const [complexity, setComplexity] = useState<Complexity>(
    limits?.academic_level === "phd" || limits?.academic_level === "researcher" ? "advanced" : "beginner",
  );
  const [run, setRun] = useState<RunLimits>({ weeks: limits?.weeks_available ?? null, compute: limits?.compute ?? null });
  const me = useMe().data;
  const plan = me?.plan;
  const shared = { complexity, constraints: typedConstraints(limits, run), plan, verified: me?.email_verified ?? true };

  return (
    <div className="flex flex-col gap-10">
      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <Pill tone="spark">Research discovery & validation</Pill>
          <MonoLabel>Every claim traced to a real sentence</MonoLabel>
        </div>
        <h1 className="display-xl max-w-5xl">
          Find the research idea <em>worth your next semester.</em>
        </h1>
        <p className="body-m max-w-2xl text-[17px] text-ink-2">
          Evaluate an idea you already have, or let Eureka map your field and propose ideas, then check every one
          against the literature, a Skeptic and your own limits.
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <EvaluateCard {...shared} />
        <DiscoverCard {...shared} />
      </div>

      <LimitsBar
        complexity={complexity}
        setComplexity={setComplexity}
        run={run}
        setRun={setRun}
        plan={plan}
        hasProfile={Boolean(limits && Object.keys(limits).length)}
      />
      <HowItWorks />
    </div>
  );
}

type Shared = { complexity: Complexity; constraints: Record<string, string>; plan: Plan | undefined; verified: boolean };

function useGoTo() {
  const router = useRouter();
  return (id: string) => router.push(`/investigations/${id}`);
}

function EvaluateCard({ complexity, constraints, plan, verified }: Shared) {
  const goTo = useGoTo();
  const create = useCreateInvestigation();
  const upload = useCreateFromDocument();
  const [idea, setIdea] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [published, setPublished] = useState(false);
  const [seedsOpen, setSeedsOpen] = useState(false);
  const [seeds, setSeeds] = useState("");
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const pending = create.isPending || upload.isPending;

  const choose = (chosen: File | undefined | null) => {
    if (!chosen) return;
    const problem = checkFile(chosen);
    setFileError(problem);
    setFile(problem ? null : chosen);
    setConsent(false);
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      if (file) {
        const form = new FormData();
        form.append("file", file);
        form.append("consent", String(consent));
        if (published) form.append("published", "true");
        form.append("complexity", complexity);
        if (Object.keys(constraints).length) form.append("constraints", JSON.stringify(constraints));
        goTo((await upload.mutateAsync(form)).id);
      } else {
        const summary = await create.mutateAsync({
          mode: "evaluate", idea: idea.trim(), seed_papers: parseSeeds(seeds), complexity, constraints,
        });
        goTo(summary.id);
      }
    } catch {
      // shown by ErrorNote
    }
  }

  const allowed = canStart(plan, "evaluate", verified);
  const ready = (file ? consent : idea.trim().length > 0) && allowed.ok;
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <MonoLabel>Mode 1</MonoLabel>
        <Pill tone={plan?.plan === "free" ? "green" : "neutral"}>{[priceText(plan, "evaluate"), "10–15 min"].filter(Boolean).join(" · ")}</Pill>
      </div>
      <h2 className="display-m">Evaluate my idea</h2>
      <p className="body-m text-ink-2">
        Eureka finds the closest work, lets a Skeptic attack your idea, and tells you what is new, what matters and
        what is doable for you.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-4">
        {file ? null : (
          <TextArea
            aria-label="Your idea"
            rows={4}
            value={idea}
            maxLength={MAX_IDEA}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="Describe your idea in a few sentences: the problem, the method, the data…"
            className="border-0 bg-paper"
          />
        )}
        <div
          onDragOver={(e: DragEvent) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e: DragEvent) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files[0]); }}
          className={cx("flex flex-col gap-3 rounded-md border border-dashed border-ink-3/60 bg-surface-muted p-4", dragging && "border-ink bg-accent-soft")}
        >
          {file ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="heading-s truncate">{file.name}</span>
                <button type="button" className="body-s text-indigo" onClick={() => { setFile(null); setConsent(false); }}>
                  Remove
                </button>
              </div>
              <label className="flex items-start gap-2 body-s">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" />
                <span>
                  Send my document&apos;s text to Claude to read it. It stays private to this investigation: never added
                  to the shared library or sent to search engines.
                </span>
              </label>
              <label className="flex items-center gap-2 body-s text-ink-2">
                <input type="checkbox" checked={published} onChange={(e) => setPublished(e.target.checked)} />
                It is already published (so it isn&apos;t counted as prior work against you)
              </label>
            </>
          ) : (
            <button type="button" onClick={() => picker.current?.click()} className="flex items-center gap-3 text-left">
              <span aria-hidden className="text-lg">⇪</span>
              <span>
                <span className="heading-s block">Or drop your draft, proposal or paper</span>
                <span className="body-s text-ink-3">.pdf · .md · .txt, up to 10 MB: private to you, sent to Claude only with your consent</span>
              </span>
            </button>
          )}
          <input ref={picker} type="file" accept={FILE_TYPES.join(",")} hidden onChange={(e) => choose(e.target.files?.[0])} />
        </div>
        {fileError ? <p className="body-s text-red">{fileError}</p> : null}
        {!file && seedsOpen ? (
          <Field label="Papers you already know (optional)" hint="DOIs, arXiv IDs or titles: one per line or separated by commas.">
            <TextArea rows={2} value={seeds} onChange={(e) => setSeeds(e.target.value)} placeholder="e.g. 2401.12345, 10.18653/v1/2024.acl-long.1" />
          </Field>
        ) : null}
        <ErrorNote error={create.error ?? upload.error} />
        {allowed.ok ? null : <PlanNote check={allowed} />}
        <div className="flex flex-wrap items-center justify-between gap-3">
          {!file && !seedsOpen ? (
            <button type="button" onClick={() => setSeedsOpen(true)} className="body-s text-left">
              <span className="font-medium text-indigo">+ Know related papers?</span>{" "}
              <span className="text-ink-3">Add DOIs, arXiv IDs or titles (optional)</span>
            </button>
          ) : <span />}
          <Button type="submit" disabled={!ready || pending}>{pending ? "Starting…" : "Evaluate idea →"}</Button>
        </div>
      </form>
    </Card>
  );
}

function DiscoverCard({ complexity, constraints, plan }: Shared) {
  const goTo = useGoTo();
  const create = useCreateInvestigation();
  const [field, setField] = useState("");
  const [interests, setInterests] = useState("");
  const [avoid, setAvoid] = useState("");
  const [count, setCount] = useState(3);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const summary = await create.mutateAsync({
        mode: "discover", field: field.trim(), interests: interests.trim() || null,
        exclusions: avoid.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 10), count, complexity, constraints,
      });
      goTo(summary.id);
    } catch {
      // shown by ErrorNote
    }
  }

  const dark = "border-0 bg-white/10 text-paper placeholder:text-ink-3 focus:border-white/40";
  const allowed = canStart(plan, "discover");
  const locked = plan?.plan === "free";
  return (
    <Card dark className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <MonoLabel className="text-accent">Mode 2</MonoLabel>
        <Pill tone="spark">{[priceText(plan, "discover"), "25–40 min"].filter(Boolean).join(" · ")}</Pill>
      </div>
      <h2 className="display-m">Find me an idea</h2>
      <p className="body-m text-ink-3">
        Tell Eureka your field and interests. It maps the literature, mines open problems and ranks grounded ideas
        that fit you.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-4 [&_label>span:first-child]:text-paper">
        <fieldset disabled={locked} className={cx("flex flex-col gap-4", locked && "opacity-40")}>
        <Field label="Field">
          <Input value={field} onChange={(e) => setField(e.target.value)} placeholder="e.g. NLP" maxLength={300} required className={dark} />
        </Field>
        <Field label="Interests">
          <Input value={interests} onChange={(e) => setInterests(e.target.value)} placeholder="e.g. LLM agents, low-resource languages" maxLength={300} className={dark} />
        </Field>
        <Field label="Avoid">
          <Input value={avoid} onChange={(e) => setAvoid(e.target.value)} placeholder="e.g. medical, robotics hardware (comma-separated)" className={dark} />
        </Field>
        </fieldset>
        <ErrorNote error={create.error} />
        {allowed.ok || locked ? null : <PlanNote check={allowed} dark />}
        <div className="flex flex-wrap items-center justify-between gap-3">
          {locked ? (
            <>
              <span className="body-s text-ink-3">Finding ideas is part of Pro.</span>
              <ButtonLink href="/pricing" kind="accent">Upgrade to Pro →</ButtonLink>
            </>
          ) : (
            <>
              <label className="flex items-center gap-2 whitespace-nowrap body-s text-ink-3">
                How many ideas
                <Input type="number" min={1} max={10} value={count} onChange={(e) => setCount(Math.min(10, Math.max(1, Number(e.target.value) || 1)))} className={cx(dark, "w-16 py-1.5")} />
              </label>
              <Button type="submit" kind="accent" disabled={!field.trim() || create.isPending || !allowed.ok}>
                {create.isPending ? "Starting…" : "Discover ideas →"}
              </Button>
            </>
          )}
        </div>
      </form>
    </Card>
  );
}

function LimitsBar({
  complexity, setComplexity, run, setRun, plan, hasProfile,
}: {
  complexity: Complexity; setComplexity: (c: Complexity) => void; run: RunLimits; setRun: (r: RunLimits) => void;
  plan: Plan | undefined; hasProfile: boolean;
}) {
  return (
    <Card className="flex flex-col gap-5 p-6 lg:flex-row lg:flex-wrap lg:items-end lg:gap-8">
      <div className="flex flex-col gap-1 lg:w-56">
        <MonoLabel>Your limits</MonoLabel>
        <span className="heading-s">Eureka judges feasibility for you</span>
        <span className="body-s text-ink-3">
          {hasProfile ? "From your profile; changes here apply to this run only." : <>Set them once in your <Link href="/profile" className="text-indigo">profile</Link>.</>}
        </span>
      </div>
      <Segmented
        label="Ambition"
        value={complexity}
        onChange={(v) => setComplexity(v ?? "beginner")}
        options={[
          { value: "beginner", label: "Beginner" },
          { value: "intermediate", label: "Intermediate" },
          { value: "advanced", label: "Advanced" },
        ]}
      />
      <LimitField label="Time (weeks)">
        <Input type="number" min={1} max={520} value={run.weeks ?? ""} placeholder="e.g. 10" className="w-28"
          onChange={(e) => setRun({ ...run, weeks: e.target.value ? Number(e.target.value) : null })} />
      </LimitField>
      <LimitField label="Compute">
        <select value={run.compute ?? ""} onChange={(e) => setRun({ ...run, compute: (e.target.value || null) as Compute | null })}
          className="rounded-md border border-line bg-surface px-3 py-2.5 body-m">
          <option value="">Not set</option>
          {(Object.keys(COMPUTE_LABELS) as Compute[]).map((c) => <option key={c} value={c}>{COMPUTE_LABELS[c]}</option>)}
        </select>
      </LimitField>
      {plan ? (
        <div className="flex flex-col gap-1.5">
          <span className="label-mono">Plan</span>
          <Link href="/profile/billing" className="py-2.5 heading-s hover:underline">{chipText(plan).join(" · ")}</Link>
        </div>
      ) : null}
    </Card>
  );
}

function LimitField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="label-mono">{label}</span>
      {children}
    </label>
  );
}

function HowItWorks() {
  // arriving from another page via "How it works" (/#how-it-works): the section only exists once the profile has
  // loaded, after the browser tried to jump to it, so it scrolls itself into view
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.location.hash === "#how-it-works") section.current?.scrollIntoView({ block: "start" });
  }, []);
  const steps = [
    ["Search", "OpenAlex and arXiv, many phrasings, newest and most cited."],
    ["Read & verify", "Every quote is checked word for word against the paper."],
    ["Skeptic", "A second agent hunts for prior work against the idea."],
    ["Verdicts by rules", "Novelty, significance, feasibility: decided by code, not vibes."],
  ];
  return (
    <section ref={section} id="how-it-works" className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
      {steps.map(([title, text], n) => (
        <div key={title} className="flex flex-col gap-1.5">
          <div className="flex items-baseline gap-2">
            <span className="font-serif text-2xl italic text-accent">{String(n + 1).padStart(2, "0")}</span>
            <span className="heading-s">{title}</span>
          </div>
          <p className="body-s text-ink-2">{text}</p>
        </div>
      ))}
    </section>
  );
}
