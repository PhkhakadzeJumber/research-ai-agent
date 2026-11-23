/** Screen 19 · Manage billing (step C). */
import type { Metadata } from "next";

import { ManageScreen } from "@/components/manage-billing";
import { MonoLabel } from "@/components/ui";

export const metadata: Metadata = { title: "Manage billing" };

export default function Page() {
  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-2.5">
        <MonoLabel>Profile / Plan &amp; billing / <span className="text-ink">Manage</span></MonoLabel>
        <h1 className="display-l">Manage <em>billing</em></h1>
      </header>
      <ManageScreen />
    </div>
  );
}
