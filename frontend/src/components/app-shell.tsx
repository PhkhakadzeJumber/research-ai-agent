/**
 * The shell of every page that needs a login (step 10a; wrapped in <Suspense> by app/(app)/layout.tsx, as Next 16
 * requires for components that read the URL): it asks the API who is logged in, sends anyone who isn't to /login
 * (remembering where they were going), and shows the top nav around the page.
 */
"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { TopNav } from "@/components/top-nav";
import { VerifyBanner } from "@/components/verify-banner";
import { ErrorNote } from "@/components/ui";
import { isUnauthorized } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";

export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  const loggedOut = isUnauthorized(me.error);

  useEffect(() => {
    if (loggedOut) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [loggedOut, path, router]);

  if (me.isPending || loggedOut) {
    return <div className="grid min-h-screen place-items-center body-s text-ink-3">Loading…</div>;
  }
  if (me.error) {
    return (
      <div className="mx-auto max-w-md p-8">
        <ErrorNote error={me.error} />
      </div>
    );
  }
  return (
    <div className="min-h-screen">
      {/* keyboard users can jump past the nav */}
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-30 focus:rounded-pill focus:bg-ink focus:px-4 focus:py-2 focus:text-paper">
        Skip to content
      </a>
      <TopNav />
      {me.data && !me.data.email_verified ? <VerifyBanner email={me.data.email} /> : null}
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-10 outline-none sm:px-10 lg:px-[120px] lg:pt-14">{children}</main>
    </div>
  );
}
