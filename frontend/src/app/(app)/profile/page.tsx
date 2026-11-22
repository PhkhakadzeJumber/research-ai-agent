/**
 * Screen 11 · Profile (step 10a): account details, change password, log out, and the research limits every new
 * investigation copies (PUT /profile replaces them as a whole).
 */
"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { initials } from "@/components/top-nav";
import { Button, Card, ErrorNote, Field, Input, MonoLabel, Segmented, TextArea } from "@/components/ui";
import {
  useChangePassword,
  useLogout,
  useMe,
  useProfile,
  useSaveProfile,
  type Me,
  type Profile,
  type ResearchLimits,
} from "@/lib/api/hooks";

type Limits = { [K in keyof ResearchLimits]-?: ResearchLimits[K] | null };

const EMPTY: Limits = {
  academic_level: null, weeks_available: null, hours_per_week: null, compute: null, compute_note: null,
  data_access: null, budget: null, skills: null, technologies: null, ethics: null, supervision: null, other: null,
};

/** What the form holds, as the API takes it: blank texts and numbers become "not set". */
export function cleanLimits(limits: Limits): ResearchLimits {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(limits)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    if (typeof value === "number" && (Number.isNaN(value) || value <= 0)) continue;
    out[key] = typeof value === "string" ? value.trim() : value;
  }
  return out as ResearchLimits;
}

const since = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { month: "short", year: "numeric" });

export default function ProfilePage() {
  const me = useMe();
  const profile = useProfile();
  if (profile.isPending || !me.data) return <p className="body-s text-ink-3">Loading…</p>;
  if (profile.error) return <ErrorNote error={profile.error} />;
  // the form starts from the loaded profile (no copying in an effect)
  return <ProfileForm me={me.data} profile={profile.data} />;
}

function ProfileForm({ me, profile }: { me: Me; profile: Profile }) {
  const save = useSaveProfile();
  const router = useRouter();
  const logout = useLogout();
  const [name, setName] = useState(profile.name ?? "");
  const [limits, setLimits] = useState<Limits>({ ...EMPTY, ...profile.limits });
  const [saved, setSaved] = useState(false);

  const set = <K extends keyof Limits>(key: K, value: Limits[K]) => {
    setSaved(false);
    setLimits((current) => ({ ...current, [key]: value }));
  };
  const text = (key: "compute_note" | "skills" | "technologies" | "ethics" | "other") => ({
    value: limits[key] ?? "",
    onChange: (e: { target: { value: string } }) => set(key, e.target.value),
  });
  const number = (key: "weeks_available" | "hours_per_week") => ({
    type: "number" as const,
    min: 0,
    step: "any",
    value: limits[key] ?? "",
    onChange: (e: { target: { value: string } }) => set(key, e.target.value === "" ? null : Number(e.target.value)),
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await save.mutateAsync({ name: name.trim() || undefined, limits: cleanLimits(limits) });
      setSaved(true);
    } catch {
      // shown by ErrorNote
    }
  }

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-2.5">
        <h1 className="display-l">
          Your profile and <em>limits</em>
        </h1>
        <p className="body-m max-w-[760px] text-ink-2">
          Eureka judges feasibility for you, so it needs to know what you can do. Every new investigation copies
          these limits; anything you type for one run overrides them for that run.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <div className="flex flex-col gap-6">
          <Card className="flex flex-col gap-4 p-6">
            <div className="flex items-center gap-3.5">
              <span className="grid size-[52px] place-items-center rounded-full bg-ink font-semibold text-paper">
                {initials(me.name, me.email)}
              </span>
              <div>
                <div className="heading-m text-lg">{me.name ?? me.email}</div>
                <div className="body-s text-ink-3">Member since {since(me.created_at)}</div>
              </div>
            </div>
            <Field label="Name">
              <Input value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} maxLength={120} />
            </Field>
            <Field label="Email" hint="Used to log in.">
              <Input value={me.email} readOnly className="text-ink-2" />
            </Field>
            <PasswordChange />
            <Button
              kind="ghost"
              className="w-fit px-3"
              onClick={async () => {
                await logout.mutateAsync().catch(() => undefined);
                router.replace("/login");
              }}
            >
              Log out
            </Button>
          </Card>
          <Card tone="accent" className="flex flex-col gap-2 p-6">
            <MonoLabel className="text-ink-2">Why we ask</MonoLabel>
            <p className="body-s">
              Time and compute are hard limits: an idea that needs a cluster or 30 weeks is marked not feasible for
              you. Skills and preferences only steer the suggestions.
            </p>
          </Card>
        </div>

        <Card>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <div className="flex items-center justify-between">
              <h2 className="heading-m">Your research limits</h2>
              <span className="body-s text-ink-3">All optional</span>
            </div>
            <Segmented
              label="Academic level"
              value={limits.academic_level}
              onChange={(v) => set("academic_level", v)}
              options={[
                { value: "undergraduate", label: "Undergraduate" },
                { value: "master", label: "Master's" },
                { value: "phd", label: "PhD" },
                { value: "researcher", label: "Researcher" },
              ]}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Weeks available" hint="Hard limit: the timeline check uses it.">
                <Input {...number("weeks_available")} placeholder="e.g. 12" />
              </Field>
              <Field label="Hours per week">
                <Input {...number("hours_per_week")} placeholder="e.g. 10" />
              </Field>
            </div>
            <Segmented
              label="Compute"
              hard
              value={limits.compute}
              onChange={(v) => set("compute", v)}
              options={[
                { value: "laptop", label: "Laptop" },
                { value: "free_notebook_gpu", label: "Free notebook GPU" },
                { value: "one_gpu", label: "One GPU for days" },
                { value: "cluster", label: "Cluster" },
              ]}
            />
            <Field label="Compute note">
              <Input {...text("compute_note")} maxLength={200} placeholder="e.g. Colab Pro, a university GPU on weekends" />
            </Field>
            <Segmented
              label="Data access"
              hard
              value={limits.data_access}
              onChange={(v) => set("data_access", v)}
              options={[
                { value: "public_only", label: "Public only" },
                { value: "can_collect", label: "Can collect" },
                { value: "institutional", label: "Institutional" },
              ]}
            />
            <div className="grid gap-5 sm:grid-cols-2">
              <Segmented
                label="Budget for paid tools"
                value={limits.budget}
                onChange={(v) => set("budget", v)}
                options={[
                  { value: "none", label: "None" },
                  { value: "small", label: "Small" },
                  { value: "some", label: "Some" },
                ]}
              />
              <Segmented
                label="Supervision"
                value={limits.supervision}
                onChange={(v) => set("supervision", v)}
                options={[
                  { value: "yes", label: "Yes" },
                  { value: "limited", label: "Limited" },
                  { value: "no", label: "No" },
                ]}
              />
            </div>
            <Field label="Skills">
              <TextArea rows={2} {...text("skills")} maxLength={1000} placeholder="e.g. Python, basic PyTorch, pandas" />
            </Field>
            <Field label="Preferred technologies and subfields">
              <TextArea rows={2} {...text("technologies")} maxLength={1000} placeholder="e.g. Hugging Face, small LMs, low-resource NLP" />
            </Field>
            <Field label="Ethics limits">
              <Input {...text("ethics")} maxLength={1000} placeholder="e.g. No human-subject data without approval" />
            </Field>
            <Field label="Anything else">
              <Input {...text("other")} maxLength={1000} placeholder="e.g. must finish before June" />
            </Field>
            <ErrorNote error={save.error} />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save limits"}
              </Button>
              <span className="body-s text-ink-3" role="status">
                {saved ? "Saved. New investigations will use these limits." : "Investigations you already ran keep the limits they used."}
              </span>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}

function PasswordChange() {
  const change = useChangePassword();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);

  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        <Button kind="secondary" className="w-fit" onClick={() => { setOpen(true); setDone(false); }}>
          Change password
        </Button>
        <p className="body-s text-ink-3">
          {done ? "Password changed. Your other sessions were logged out." : "Changing your password logs you out everywhere else."}
        </p>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-3 rounded-md bg-paper p-4"
      onSubmit={async (event) => {
        event.preventDefault();
        try {
          await change.mutateAsync({ current, new: next });
          setOpen(false);
          setDone(true);
          setCurrent("");
          setNext("");
        } catch {
          // shown by ErrorNote
        }
      }}
    >
      <Field label="Current password">
        <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
      </Field>
      <Field label="New password" hint="At least 10 characters">
        <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
      </Field>
      <ErrorNote error={change.error} />
      <div className="flex gap-2">
        <Button type="submit" disabled={change.isPending}>Save password</Button>
        <Button kind="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}
