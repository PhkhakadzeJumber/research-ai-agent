/**
 * Screen 09 · Library (step 10d): the papers Eureka has found and read, shared across investigations (a paper is
 * read and paid for once). Search titles and abstracts; filter to papers read closely, papers found in your own
 * investigations, or recent ones. The counts only ever show your own investigations (step 9d).
 */
"use client";

import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { Button, Card, ErrorNote, Input, MonoLabel, Pill, cx } from "@/components/ui";
import { useLibrary, type LibraryFilters } from "@/lib/api/hooks";

const CHIPS: { label: string; apply: (f: LibraryFilters) => LibraryFilters; on: (f: LibraryFilters) => boolean }[] = [
  { label: "Read closely", apply: (f) => ({ ...f, read: f.read ? null : true }), on: (f) => f.read === true },
  { label: "Used in my investigations", apply: (f) => ({ ...f, used: f.used ? null : true }), on: (f) => f.used === true },
  { label: "2024 and later", apply: (f) => ({ ...f, yearFrom: f.yearFrom ? null : 2024 }), on: (f) => f.yearFrom === 2024 },
];

export default function LibraryPage() {
  const [filters, setFilters] = useState<LibraryFilters>({ q: "", read: true, used: null, yearFrom: null });
  const [pages, setPages] = useState(1);
  const deferred = useDeferredValue(filters); // typing doesn't fire a request per keystroke while one is pending
  const library = useLibrary(deferred, pages);
  const items = library.data?.items ?? [];
  const total = library.data?.total ?? 0;
  const change = (next: LibraryFilters) => {
    setFilters(next);
    setPages(1);
  };

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-col gap-2.5">
        <h1 className="display-l">The library <em>Eureka has read.</em></h1>
        <p className="body-m max-w-[700px] text-ink-2">
          Papers Eureka has found and read closely, with what each one found and the quotes checked word for word.
          Shared across your investigations, so nothing is paid for twice.
        </p>
      </header>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <label className="flex flex-1 items-center gap-2.5 rounded-pill border border-line bg-surface px-4 py-2.5">
          <span aria-hidden className="text-ink-3">⌕</span>
          <span className="sr-only">Search the library</span>
          <Input value={filters.q} onChange={(e) => change({ ...filters, q: e.target.value })} placeholder="Search titles and abstracts…"
            maxLength={200} className="border-0 bg-transparent p-0 focus:border-0" />
        </label>
        <div className="flex flex-wrap gap-2">
          {CHIPS.map((chip) => (
            <button key={chip.label} aria-pressed={chip.on(filters)} onClick={() => change(chip.apply(filters))}
              className={cx("rounded-pill px-3.5 py-2 label-s text-[13px]", chip.on(filters) ? "bg-ink text-paper" : "border border-line bg-surface text-ink-2")}>
              {chip.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <MonoLabel>{library.isPending ? "Searching…" : `${total} paper${total === 1 ? "" : "s"}`}</MonoLabel>
        {library.isFetching && !library.isPending ? <span className="body-s text-ink-3">Updating…</span> : null}
      </div>
      {library.error ? <ErrorNote error={library.error} /> : null}
      {!library.isPending && items.length === 0 ? (
        <Card className="flex flex-col gap-1.5">
          <span className="heading-s">No papers match</span>
          <span className="body-s text-ink-2">Try other words, or turn off a filter.</span>
        </Card>
      ) : null}

      <div className="flex flex-col gap-3">
        {items.map((paper) => (
          <Link key={paper.id} href={`/library/${paper.id}`} className="block rounded-lg focus-visible:outline-offset-4">
            <Card className="flex flex-col gap-2.5 p-5 transition hover:border-ink-3">
              <div className="flex flex-wrap items-center gap-2.5">
                <MonoLabel>{paper.peer_reviewed ? paper.venue ?? "Journal / conference" : "Preprint"} · {paper.year ?? "n.d."}</MonoLabel>
                {paper.citation_count ? <span className="body-s text-ink-3">{paper.citation_count} citations</span> : null}
              </div>
              <h2 className="heading-m text-lg">{paper.title}</h2>
              <div className="flex flex-wrap gap-2">
                {paper.read ? (
                  <Pill tone="green">Read · {paper.verified_quotes} verified quote{paper.verified_quotes === 1 ? "" : "s"}</Pill>
                ) : <Pill>Found, not read</Pill>}
                {paper.read && paper.source_level === "abstract" ? <Pill tone="amber">abstract only</Pill> : null}
                {paper.investigations ? (
                  <Pill tone="indigo">Used in {paper.investigations} of your investigation{paper.investigations === 1 ? "" : "s"}</Pill>
                ) : null}
              </div>
            </Card>
          </Link>
        ))}
      </div>
      {items.length < total ? (
        <div><Button kind="secondary" disabled={library.isFetching} onClick={() => setPages(pages + 1)}>Show more</Button></div>
      ) : null}
    </div>
  );
}
