/**
 * The "verify your email" banner (step B; Figma 01b): shown under the top nav until the email is verified, with a
 * button that sends a new link (at most one a minute: the server says so).
 */
"use client";

import { useResendVerification } from "@/lib/api/hooks";

export function VerifyBanner({ email }: { email: string }) {
  const resend = useResendVerification();
  const status = resend.isSuccess ? "Sent: check your inbox (and spam)." : resend.error instanceof Error ? resend.error.message : null;
  return (
    <div role="status" className="mx-auto mt-4 flex w-[calc(100%-2rem)] max-w-[1200px] flex-wrap items-center gap-3 rounded-md bg-amber-soft px-5 py-3.5 body-s sm:w-[calc(100%-5rem)]">
      <span className="flex-1">
        Verify your email to start your free evaluation: we sent a link to <span className="font-semibold">{email}</span>.
        {status ? <span className="ml-1 text-ink-2">{status}</span> : null}
      </span>
      <button type="button" disabled={resend.isPending || resend.isSuccess} onClick={() => resend.mutate()}
        className="font-semibold text-amber disabled:opacity-60">
        {resend.isPending ? "Sending…" : "Resend the link"}
      </button>
    </div>
  );
}
