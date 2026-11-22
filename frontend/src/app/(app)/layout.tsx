/**
 * Every page that needs a login (step 10a): it asks the API who is logged in, sends anyone who isn't to /login
 * (remembering where they were going), and shows the top nav around the page.
 */
"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { TopNav } from "@/components/top-nav";
import { ErrorNote } from "@/components/ui";
import { isUnauthorized } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";

export default function AppLayout({ children }: { children: ReactNode }) {
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
      <TopNav />
      <main className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-10 sm:px-10 lg:px-[120px] lg:pt-14">{children}</main>
    </div>
  );
}
