import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPassword } from "@/components/email-pages";

export const metadata: Metadata = { title: "Set a new password" };

export default function Page() {
  return (
    <Suspense>
      <ResetPassword />
    </Suspense>
  );
}
