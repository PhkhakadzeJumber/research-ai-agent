/** Screen 05 · Idea detail (step 10c): see components/idea.tsx. */
"use client";

import { useParams } from "next/navigation";

import { IdeaDetail } from "@/components/idea";

export default function IdeaPage() {
  const { id } = useParams<{ id: string }>();
  return <IdeaDetail ideaId={id} />;
}
