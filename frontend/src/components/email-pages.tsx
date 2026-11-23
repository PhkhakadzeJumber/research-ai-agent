/**
 * The pages emailed links open (step B; Figma 14, 15, 16): forgot password (asks for a link), reset password (sets
 * the new one from the link) and verify email (confirms the address from the link). They share the log-in layout.
 */
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { AuthLayout } from "@/components/auth-screen";
import { Button, ButtonLink, ErrorNote, Field, Input, Logo } from "@/components/ui";
import { useRequestReset, useResetPassword, useVerifyEmail } from "@/lib/api/hooks";

function Frame({ back, title, intro, children }: { back: [string, string]; title: string; intro: string; children: React.ReactNode }) {
  return (
    <AuthLayout>
      <div className="flex w-full max-w-[400px] flex-col gap-5">
        <Logo className="lg:hidden" />
        <Link href={back[0]} className="w-fit body-s font-medium text-indigo">← {back[1]}</Link>
        <div className="flex flex-col gap-1.5">
          <h1 className="display-m">{title}</h1>
          <p className="body-s text-ink-2">{intro}</p>
        </div>
        {children}
      </div>
    </AuthLayout>
  );
}

export function ForgotPassword() {
  const request = useRequestReset();
  const [email, setEmail] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    try { await request.mutateAsync(email.trim()); } catch { /* shown by ErrorNote */ }
  }
  return (
    <Frame back={["/login", "Back to log in"]} title="Forgot your password?"
      intro="Enter your account's email and we'll send you a link to set a new one. It works once, for an hour.">
      {request.isSuccess ? (
        <p role="status" className="rounded-md bg-green-soft px-4 py-3 body-s text-green">
          If {email} has an account, the link is on its way. Check your spam folder too.
        </p>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-5">
          <Field label="Email">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@university.edu" autoComplete="email" required />
          </Field>
          <ErrorNote error={request.error} />
          <Button type="submit" disabled={request.isPending} className="w-full">{request.isPending ? "Sending…" : "Send the link →"}</Button>
        </form>
      )}
    </Frame>
  );
}

export function ResetPassword() {
  const token = useSearchParams().get("token") ?? "";
  const reset = useResetPassword();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const mismatch = again.length > 0 && again !== password;
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await reset.mutateAsync({ token, password });
      router.replace("/");
    } catch { /* shown by ErrorNote */ }
  }
  return (
    <Frame back={["/login", "Back to log in"]} title="Set a new password"
      intro="Saving it logs you out on your other devices.">
      {token ? (
        <form onSubmit={submit} className="flex flex-col gap-5">
          <Field label="New password" hint="At least 10 characters">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} autoComplete="new-password" required />
          </Field>
          <Field label="The same password again" hint={mismatch ? "The two passwords differ." : undefined}>
            <Input type="password" value={again} onChange={(e) => setAgain(e.target.value)} minLength={10} autoComplete="new-password" required />
          </Field>
          <ErrorNote error={reset.error} />
          <Button type="submit" disabled={reset.isPending || mismatch || password.length < 10 || again !== password} className="w-full">
            {reset.isPending ? "Saving…" : "Save and log in →"}
          </Button>
          {password.length > 0 && password.length < 10 ? (
            <p className="-mt-2 body-s text-amber">{10 - password.length} more character{10 - password.length === 1 ? "" : "s"} needed.</p>
          ) : password.length >= 10 && again !== password && !mismatch ? (
            <p className="-mt-2 body-s text-ink-3">Type it again in the second box to save.</p>
          ) : null}
          <p className="body-s text-ink-3">The link in your email works once, for an hour. Expired? <Link href="/forgot-password" className="text-indigo">Ask for a new one</Link>.</p>
        </form>
      ) : (
        <p className="body-s text-ink-2">This page opens from the link in your email. <Link href="/forgot-password" className="text-indigo">Ask for a link</Link>.</p>
      )}
    </Frame>
  );
}

export function VerifyEmail() {
  const token = useSearchParams().get("token") ?? "";
  const verify = useVerifyEmail();
  const started = useRef(false);
  useEffect(() => {
    // opening the link verifies at once (once: React may run effects twice in development)
    if (token && !started.current) {
      started.current = true;
      verify.mutate(token);
    }
  }, [token, verify]);
  return (
    <Frame back={["/", "Back to Eureka"]} title={verify.isSuccess ? "Your email is verified" : verify.isError ? "This link didn't work" : "Checking your link…"}
      intro={verify.isSuccess ? "Thanks! Your free evaluation is ready to start." : "One moment."}>
      {verify.isSuccess ? <ButtonLink href="/" kind="primary" className="w-fit">Evaluate an idea →</ButtonLink> : null}
      {verify.isError ? (
        <>
          <ErrorNote error={verify.error} />
          <p className="body-s text-ink-2">Log in and use “Resend the link” at the top of the page for a new one.</p>
        </>
      ) : null}
      {!token ? <p className="body-s text-ink-2">This page opens from the link in your email.</p> : null}
    </Frame>
  );
}
