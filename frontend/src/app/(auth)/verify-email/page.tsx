import type { Metadata } from "next";
import { Suspense } from "react";

import { VerifyEmail } from "@/components/email-pages";

export const metadata: Metadata = { title: "Verify your email" };

export default function Page() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
