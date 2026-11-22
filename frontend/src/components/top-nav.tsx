/**
 * The top navigation (Figma component TopNav): the logo, the main links (the current one in ink), the plan chip
 * (credits left, or the free evaluation; it opens Plan & billing; step P4), and the avatar, which opens the profile.
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useMe } from "@/lib/api/hooks";
import { chipText } from "@/lib/plan";

import { Logo, cx } from "./ui";

const LINKS = [
  { href: "/investigations", label: "Investigations" },
  { href: "/", label: "New" },
  { href: "/library", label: "Library" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/pricing", label: "Pricing" },
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
  const [menuOpen, setMenuOpen] = useState(false);
  const active = (href: string) => (href.startsWith("/#") ? false : href === "/" ? path === "/" : path.startsWith(href));
  return (
    <header
      className="relative flex h-[72px] items-center justify-between border-b border-line px-4 sm:px-8"
      onKeyDown={(e) => { if (e.key === "Escape") setMenuOpen(false); }}
    >
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
        <button
          type="button"
          className="rounded-pill border border-line bg-surface px-3 py-1.5 text-sm sm:hidden"
          aria-expanded={menuOpen}
          aria-controls="phone-menu"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? "Close" : "Menu"}
        </button>
        {me ? (
          <Link href="/profile/billing" aria-label="Your plan and credits"
            className="hidden items-center gap-2 rounded-pill border border-line bg-surface px-3 py-1 hover:border-ink-3 sm:inline-flex">
            <span className="label-mono">{chipText(me.plan)[0]}</span>
            <span className="text-sm font-semibold">{chipText(me.plan)[1]}</span>
          </Link>
        ) : null}
        <Link
          href="/profile"
          aria-label="Your profile"
          className="grid size-[34px] place-items-center rounded-full bg-ink text-[11px] font-semibold text-paper"
        >
          {me ? initials(me.name, me.email) : ""}
        </Link>
      </div>
      {menuOpen ? (
        // phones: the links the wide nav shows, plus this month's spend
        <nav id="phone-menu" className="absolute inset-x-0 top-[72px] z-20 flex flex-col border-b border-line bg-paper px-4 py-2 shadow-sm sm:hidden">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} onClick={() => setMenuOpen(false)}
              aria-current={active(link.href) ? "page" : undefined}
              className={cx("py-3 text-base", active(link.href) ? "font-semibold text-ink" : "text-ink-2")}>
              {link.label}
            </Link>
          ))}
          {me ? (
            <Link href="/profile/billing" onClick={() => setMenuOpen(false)} className="py-3 body-s text-ink-3">
              {chipText(me.plan).join(" · ")}
            </Link>
          ) : null}
        </nav>
      ) : null}
    </header>
  );
}
