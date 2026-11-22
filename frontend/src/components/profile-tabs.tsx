/** The two halves of the profile (step P4): the research limits, and the plan with its credits. */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cx } from "@/components/ui";

const TABS = [
  { href: "/profile", label: "Profile & limits" },
  { href: "/profile/billing", label: "Plan & billing" },
];

export function ProfileTabs() {
  const path = usePathname();
  return (
    <nav aria-label="Profile" className="flex w-fit gap-1 rounded-pill bg-surface-muted p-1">
      {TABS.map((tab) => (
        <Link key={tab.href} href={tab.href} aria-current={path === tab.href ? "page" : undefined}
          className={cx("rounded-pill px-4 py-2 label-s", path === tab.href ? "bg-ink text-paper" : "text-ink-2 hover:text-ink")}>
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
