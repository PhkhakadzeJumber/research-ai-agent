/**
 * Why the plan doesn't allow something, and what would (step P4): "This needs 25 credits; you have 12. See plans →".
 * Shown before the click, so the user isn't sent into a request the server would refuse.
 */
import Link from "next/link";

import { cx } from "@/components/ui";
import type { Check } from "@/lib/plan";

export function PlanNote({ check, dark = false }: { check: Check; dark?: boolean }) {
  if (check.ok) return null;
  return (
    <p className={cx("rounded-md px-4 py-3 body-s", dark ? "bg-white/10 text-paper" : "bg-amber-soft text-amber")}>
      {check.reason}{" "}
      {check.upgrade ? <Link href="/pricing" className="font-semibold underline">See plans →</Link> : null}
    </p>
  );
}
