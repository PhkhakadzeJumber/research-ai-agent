/**
 * The frame of the public Pricing page (step P4): the usual top nav when logged in, otherwise the logo with Log in
 * and Sign up. Unlike AppShell it never sends anyone to /login.
 */
"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { TopNav } from "@/components/top-nav";
import { ButtonLink, Logo } from "@/components/ui";
import { useMe } from "@/lib/api/hooks";

export function PricingShell({ children }: { children: ReactNode }) {
  const me = useMe();
  return (
    <div className="min-h-screen">
      {me.data ? <TopNav /> : (
        <header className="flex h-[72px] items-center justify-between border-b border-line px-4 sm:px-8">
          <Link href="/" aria-label="Eureka home"><Logo /></Link>
          <div className="flex items-center gap-2.5">
            <ButtonLink href="/login" kind="ghost">Log in</ButtonLink>
            <ButtonLink href="/signup" kind="primary">Sign up</ButtonLink>
          </div>
        </header>
      )}
      <main id="main" className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-10 sm:px-10 lg:pt-16">{children}</main>
    </div>
  );
}
