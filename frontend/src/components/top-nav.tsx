/**
 * The top navigation (Figma component TopNav): the logo, the main links (the current one in ink), this month's
 * spend, and the avatar, which opens the profile. Step 10a.
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useMe } from "@/lib/api/hooks";

import { Logo, cx } from "./ui";

const LINKS = [
  { href: "/investigations", label: "Investigations" },
  { href: "/", label: "New" },
  { href: "/library", label: "Library" },
  { href: "/#how-it-works", label: "How it works" },
];

export function initials(name: string | null | undefined, email: string | undefined) {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (email ?? "?").slice(0, 2).toUpperCase();
}

export function TopNav() {
  const path = usePathname();
  const { data: me } = useMe();
  const active = (href: string) => (href.startsWith("/#") ? false : href === "/" ? path === "/" : path.startsWith(href));
  return (
    <header className="flex h-[72px] items-center justify-between border-b border-line px-4 sm:px-8">
      <div className="flex items-center gap-6 sm:gap-10">
        <Link href="/" aria-label="Eureka home">
          <Logo />
        </Link>
        <nav className="hidden items-center gap-6 sm:flex">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active(link.href) ? "page" : undefined}
              className={cx("text-sm", active(link.href) ? "font-semibold text-ink" : "text-ink-2 hover:text-ink")}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-3">
        {me ? (
          <span className="hidden items-center gap-2 rounded-pill border border-line bg-surface px-3 py-1 sm:inline-flex">
            <span className="label-mono">This month</span>
            <span className="text-sm font-semibold">${me.spent_this_month_usd.toFixed(2)}</span>
          </span>
        ) : null}
        <Link
          href="/profile"
          aria-label="Your profile"
          className="grid size-[34px] place-items-center rounded-full bg-ink text-[11px] font-semibold text-paper"
        >
          {me ? initials(me.name, me.email) : ""}
        </Link>
      </div>
    </header>
  );
}
