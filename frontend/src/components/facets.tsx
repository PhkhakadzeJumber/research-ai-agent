/**
 * "The idea, in parts" (step 10d fix after hand testing): the facets grouped by kind, so a kind the Idea Analyst
 * split in two (it may: up to 2 per kind) shows its label once with both parts under it, key parts first, in the
 * order problem → method → data → evaluation → contribution. Used by the clarification (editable) and the idea
 * detail (read-only), so both pages show an idea the same way.
 */
"use client";

import { Pill, TextArea } from "@/components/ui";
import { KIND_LABELS } from "@/lib/format";

export type FacetItem = { position: number; kind: string; text: string; is_key: boolean; stated_by_user?: boolean; from_section?: string | null };

const ORDER = ["problem", "method", "data_setting", "evaluation", "contribution"];

/** Facets grouped by kind, in the design's order; within a kind, key parts first, then as A1 listed them. */
export function groupFacets<F extends FacetItem>(facets: F[]): [string, F[]][] {
  const groups = new Map<string, F[]>();
  for (const facet of facets) groups.set(facet.kind, [...(groups.get(facet.kind) ?? []), facet]);
  const rank = (kind: string) => (ORDER.includes(kind) ? ORDER.indexOf(kind) : ORDER.length);
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([kind, list]) => [kind, [...list].sort((x, y) => Number(y.is_key) - Number(x.is_key) || x.position - y.position)]);
}

export function FacetGroups<F extends FacetItem>({
  facets,
  edits,
  editing = false,
  onEdit,
}: {
  facets: F[];
  edits?: Record<number, string>;
  editing?: boolean;
  onEdit?: (position: number, text: string) => void;
}) {
  return (
    <dl className="flex flex-col">
      {groupFacets(facets).map(([kind, parts]) => (
        <div key={kind} className="grid gap-x-5 gap-y-1.5 border-t border-line py-3 sm:grid-cols-[120px_1fr]">
          <dt className="label-mono pt-0.5 text-ink-2">
            {KIND_LABELS[kind] ?? kind}
            {parts.length > 1 ? <span className="text-ink-3"> · {parts.length}</span> : null}
          </dt>
          <dd className="flex flex-col gap-2.5">
            {parts.map((facet) => {
              const text = edits?.[facet.position]?.trim() || facet.text;
              return (
                <div key={facet.position} className="flex flex-col gap-1.5">
                  {editing && onEdit ? (
                    <TextArea rows={2} value={edits?.[facet.position] ?? facet.text}
                      onChange={(e) => onEdit(facet.position, e.target.value)} aria-label={`${KIND_LABELS[kind] ?? kind} text`} />
                  ) : (
                    <p className="body-m">{text}</p>
                  )}
                  {facet.is_key || facet.stated_by_user === false || facet.from_section ? (
                    <span className="flex flex-wrap gap-1.5">
                      {facet.is_key ? <Pill tone="spark">key</Pill> : null}
                      {facet.stated_by_user === false ? <Pill tone="amber">proposed by Eureka</Pill> : null}
                      {facet.from_section ? <Pill tone="indigo">from {facet.from_section}</Pill> : null}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </dd>
        </div>
      ))}
    </dl>
  );
}
