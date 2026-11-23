import type { Metadata } from "next";
import { Suspense } from "react";

import { ForgotPassword } from "@/components/email-pages";

export const metadata: Metadata = { title: "Forgot password" };

export default function Page() {
  return (
    <Suspense>
      <ForgotPassword />
    </Suspense>
  );
}
