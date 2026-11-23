/**
 * Screen 20 · Terms, Privacy and Refunds (step D; Figma): one layout, a side menu between the three, the text from
 * LEGAL below. Public pages (no login). The rules they state match what the site does: docs/18 (plans, credits,
 * refunds) and the upload consent (doc 02 §2.4).
 */
import Link from "next/link";

import { Card, MonoLabel, cx } from "@/components/ui";

export type LegalPage = "terms" | "privacy" | "refunds";

type Doc = { title: [string, string]; sections: [string, string[]][] };

const UPDATED = "23 November 2026";

export const LEGAL: Record<LegalPage, Doc> = {
  terms: {
    title: ["Terms of", "Service"],
    sections: [
      ["1. What Eureka is", ["Eureka helps you evaluate research ideas and find new ones by searching the academic literature, reading the most relevant papers, and checking each idea against them. Its verdicts are evidence-backed opinions, not certificates of originality: you and your supervisor make the final call."]],
      ["2. Your account", ["Keep your password safe; you are responsible for what happens under your account. One person per account. Free evaluations are for personal use, and we may suspend accounts made to get around the free plan's limits."]],
      ["3. Your documents and ideas", ["What you upload or type stays yours. Uploaded documents are private to the investigation you uploaded them for: they are never added to the shared paper library or sent to search engines, and they are sent to our AI provider only with your consent, to read them. See the Privacy Policy."]],
      ["4. Plans, credits and payments", ["Prices in credits are shown before every run. A run is charged what it used, never more than its price; credits for runs that fail on our side are returned. Pro renews until you cancel; after cancelling, Pro lasts until the end of the period you paid for. See the Refund Policy."]],
      ["5. Acceptable use", ["Don't use Eureka to break the law, to overload or attack the service, to scrape it, or to resell its results as your own service. Don't upload documents you have no right to share."]],
      ["6. Results and liability", ["Eureka searches public sources and can miss work that exists. Use its results as a starting point and check them; we aren't liable for decisions made on them. The service is provided as is."]],
      ["7. Changes", ["We may update these terms; we'll tell you by email before important changes take effect."]],
    ],
  },
  privacy: {
    title: ["Privacy", "Policy"],
    sections: [
      ["1. What we keep", ["Your account (name, email, a hash of your password, never the password itself), your research limits, your investigations and what Eureka found for them, and your plan, credits and receipts. For payments we keep only the card's brand, last four digits and expiry; the full card number never reaches us."]],
      ["2. Your documents", ["A document you upload is stored only for its investigation. It is never added to the shared paper library, never sent to search engines, and never shown to other users. It is sent to our AI provider only after you agree, to read it."]],
      ["3. Who processes it", ["Our AI provider (to read papers and your documents and write the evaluations), our email provider (verification and password-reset emails), our hosting and database providers. Each processes data only to provide its service to us."]],
      ["4. What other users see", ["Nothing of yours. The paper library is shared, but which investigations used a paper is shown only to their owner."]],
      ["5. Cookies", ["One cookie keeps you logged in (HttpOnly, sent only over HTTPS). No advertising or tracking cookies."]],
      ["6. Your rights", ["You can see your data in the app, change your profile at any time, and ask us to export or delete your account and everything in it."]],
    ],
  },
  refunds: {
    title: ["Refund", "Policy"],
    sections: [
      ["1. Runs that fail", ["If a run fails on our side (an error, our AI provider being unavailable, a server restart), every credit it reserved comes back automatically. You don't need to ask."]],
      ["2. Runs you stop", ["If you stop a run, you pay only for what it used; the rest of its price comes back. You can resume it later for what is left of the price."]],
      ["3. Pro", ["You can cancel Pro at any time in Manage billing; it then lasts until the end of the period you paid for. If you were charged for a renewal you didn't want, ask within 14 days and we'll refund it if you haven't used that period's credits."]],
      ["4. Top-ups", ["Unused top-up credits can be refunded within 14 days of purchase."]],
      ["5. How to ask", ["Write to us from the email of your account with the receipt from Manage billing."]],
    ],
  },
};

const MENU: [LegalPage, string][] = [["terms", "Terms of Service"], ["privacy", "Privacy Policy"], ["refunds", "Refund Policy"]];

export function LegalScreen({ page }: { page: LegalPage }) {
  const doc = LEGAL[page];
  return (
    <div className="grid gap-10 lg:grid-cols-[220px_1fr]">
      <nav aria-label="Legal" className="flex flex-col gap-1 self-start lg:sticky lg:top-6">
        <MonoLabel className="mb-1">Legal</MonoLabel>
        {MENU.map(([key, label]) => (
          <Link key={key} href={`/${key}`} aria-current={key === page ? "page" : undefined}
            className={cx("rounded-sm px-3 py-2 body-s", key === page ? "border border-line bg-surface font-semibold text-ink" : "text-ink-2 hover:text-ink")}>
            {label}
          </Link>
        ))}
      </nav>
      <Card className="flex min-w-0 flex-col gap-5 p-6 sm:p-12">
        <h1 className="display-l">{doc.title[0]} <em>{doc.title[1]}</em></h1>
        <p className="body-s text-ink-3">Last updated {UPDATED}</p>
        {doc.sections.map(([heading, paragraphs]) => (
          <section key={heading} className="flex flex-col gap-2">
            <h2 className="heading-m">{heading}</h2>
            {paragraphs.map((p) => <p key={p} className="body-m text-ink-2">{p}</p>)}
          </section>
        ))}
      </Card>
    </div>
  );
}
