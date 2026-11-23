/**
 * Screen 08 · Log in / Sign up (step 10a): the dark brand panel on the left, the form on the right. One component for
 * both: the tabs switch between /login and /signup. After logging in it goes back where the user was headed (?next=).
 * AuthLayout (the two panels) is shared with the email pages (step B: forgot / reset password, verify email).
 */
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";

import { Button, ErrorNote, Field, Input, Logo, cx } from "@/components/ui";
import { useLogin, useSignup } from "@/lib/api/hooks";

export function safeNext(next: string | null) {
  // only paths on this site: never send a user to another site after logging in
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export function AuthScreen({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const login = useLogin();
  const signup = useSignup();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const action = mode === "login" ? login : signup;

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      if (mode === "login") await login.mutateAsync({ email, password });
      else await signup.mutateAsync({ name, email, password });
      router.replace(next);
    } catch {
      // shown by ErrorNote
    }
  }

  const tab = (target: "login" | "signup", label: string) => (
    <Link
      href={`/${target}${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`}
      aria-current={mode === target ? "page" : undefined}
      className={cx("flex-1 rounded-pill py-2 text-center text-sm", mode === target ? "bg-surface font-medium text-ink" : "text-ink-3")}
    >
      {label}
    </Link>
  );

  return (
    <AuthLayout>
      <form onSubmit={submit} className="flex w-full max-w-[400px] flex-col gap-5">
        <Logo className="lg:hidden" />
        <nav className="flex gap-1 rounded-pill bg-surface-muted p-1">
          {tab("login", "Log in")}
          {tab("signup", "Sign up")}
        </nav>
        <div className="flex flex-col gap-1.5">
          <h2 className="display-m">{mode === "login" ? "Welcome back" : "Create your account"}</h2>
          <p className="body-s text-ink-2">
            {mode === "login"
              ? "Log in to see your investigations."
              : "Free to start: one full evaluation every month. Pro adds discovery and research packages."}
          </p>
        </div>
        {mode === "signup" ? (
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" autoComplete="name" required />
          </Field>
        ) : null}
        <Field label="Email">
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@university.edu" autoComplete="email" required />
        </Field>
        <Field label="Password" hint={mode === "signup" ? "At least 10 characters" : undefined}>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={mode === "signup" ? 10 : undefined}
            required
          />
        </Field>
        {mode === "login" ? (
          <Link href="/forgot-password" className="-mt-2 w-fit body-s font-medium text-indigo">Forgot password?</Link>
        ) : (
          <label className="flex items-start gap-2.5 body-s text-ink-2">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} required className="mt-0.5" />
            <span>
              I agree to the <Link href="/terms" className="text-indigo">Terms of Service</Link> and the{" "}
              <Link href="/privacy" className="text-indigo">Privacy Policy</Link>
            </span>
          </label>
        )}
        <ErrorNote error={action.error} />
        <Button type="submit" disabled={action.isPending || (mode === "signup" && !agreed)} className="w-full">
          {action.isPending ? "One moment…" : mode === "login" ? "Log in →" : "Create account →"}
        </Button>
        <p className="body-s text-ink-3">
          Your uploaded drafts stay private: they are never added to the shared library or sent to search engines.
        </p>
      </form>
    </AuthLayout>
  );
}

/** The two panels of the account pages: the brand on the left (wide screens), the form on the right. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_640px]">
      <section className="hidden flex-col justify-between bg-ink px-[72px] py-14 text-paper lg:flex">
        <Logo className="[&_span]:text-paper" />
        <div className="flex flex-col gap-6">
          <h1 className="display-xl max-w-xl text-[64px] leading-[68px]">
            Every verdict comes with its <em className="text-accent">evidence.</em>
          </h1>
          <p className="max-w-[520px] text-[17px] leading-relaxed text-ink-3">
            Eureka reads the literature, verifies every quote word for word, and lets a Skeptic attack your idea
            before you spend a semester on it.
          </p>
        </div>
        <dl className="flex gap-10">
          {[
            ["825", "papers screened per idea"],
            ["3", "Skeptic rounds"],
            ["100%", "quotes checked"],
          ].map(([value, label]) => (
            <div key={label} className="flex flex-col gap-1">
              <dd className="display-l text-[40px]">{value}</dd>
              <dt className="label-mono">{label}</dt>
            </div>
          ))}
        </dl>
      </section>

      <section className="flex items-center justify-center px-4 py-12">
        {children}
      </section>
    </div>
  );
}
