/** Screen 06 · Research package (step 10c): see components/package.tsx. */
"use client";

import { useParams } from "next/navigation";

import { PackageScreen } from "@/components/package";

export default function PackagePage() {
  const { id } = useParams<{ id: string }>();
  return <PackageScreen ideaId={id} />;
}
