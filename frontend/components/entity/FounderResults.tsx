"use client";

import { getFounders, type QueryParams } from "@/lib/api";
import type { PersonCard } from "@/lib/types";
import { FounderRow } from "./FounderList";
import { LoadMore } from "./LoadMore";

/** Ranked founder list with offset pagination over GET /api/founders. */
export function FounderResults({ initial, query, pageSize }: { initial: PersonCard[]; query: QueryParams; pageSize: number }) {
  return (
    <LoadMore
      initial={initial}
      query={query}
      pageSize={pageSize}
      fetchPage={(p) => getFounders(p)}
      render={(items) => (
        <ol className="border-t border-ink">
          {items.map((p, i) => (
            <FounderRow key={p.id} person={p} rank={i + 1} />
          ))}
        </ol>
      )}
      noun="people"
    />
  );
}
