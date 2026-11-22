/** Screen 01 · Start. Built in step 10b; for now a placeholder so the logged-in shell has a home. */
import { ButtonLink } from "@/components/ui";

export default function StartPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="display-xl max-w-4xl">
        Find the research idea <em>worth your next semester.</em>
      </h1>
      <p className="body-m max-w-2xl text-ink-2">The start screen arrives in step 10b. Meanwhile, set your research limits.</p>
      <div>
        <ButtonLink href="/profile">Your profile</ButtonLink>
      </div>
    </div>
  );
}
