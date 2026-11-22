import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthScreen } from "@/components/auth-screen";

export const metadata: Metadata = { title: "Log in" };

export default function Page() {
  return (
    <Suspense>
      <AuthScreen mode="login" />
    </Suspense>
  );
}
