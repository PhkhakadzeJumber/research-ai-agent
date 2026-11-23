/** The footer of every page (step D; Figma 20): the logo, Pricing and the legal pages. */
import Link from "next/link";

import { Logo } from "@/components/ui";

const LINKS = [["/pricing", "Pricing"], ["/terms", "Terms"], ["/privacy", "Privacy"], ["/refunds", "Refunds"]];

export function Footer() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto flex w-full max-w-[1440px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-7 sm:px-10 lg:px-[120px]">
        <Link href="/" aria-label="Eureka home"><Logo /></Link>
        <nav aria-label="Footer" className="ml-auto flex flex-wrap items-center gap-x-6 gap-y-2">
          {LINKS.map(([href, label]) => <Link key={href} href={href} className="body-s text-ink-2 hover:text-ink">{label}</Link>)}
          <span className="body-s text-ink-3">© 2026 Eureka</span>
        </nav>
      </div>
    </footer>
  );
}
