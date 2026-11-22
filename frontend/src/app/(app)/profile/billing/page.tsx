/** Screen 13 · Plan & billing (step P4): the second tab of the profile. */
import type { Metadata } from "next";

import { BillingScreen } from "@/components/billing";
import { ProfileTabs } from "@/components/profile-tabs";

export const metadata: Metadata = { title: "Plan & billing" };

export default function Page() {
  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-4">
        <h1 className="display-l">Your plan and <em>credits</em></h1>
        <ProfileTabs />
      </header>
      <BillingScreen />
    </div>
  );
}
