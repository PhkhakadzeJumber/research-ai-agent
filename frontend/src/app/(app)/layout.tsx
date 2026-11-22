/** Every page that needs a login: the shell (login check, top nav) reads the URL, so it sits inside <Suspense>. */
import { Suspense, type ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center body-s text-ink-3">Loading…</div>}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
