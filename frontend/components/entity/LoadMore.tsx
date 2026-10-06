"use client";

import { useState, type ReactNode } from "react";
import type { QueryParams } from "@/lib/api";

/**
 * Generic offset pagination: renders `render(items)` and a "Load more"
 * control that fetches the next page with the same query. Key this component
 * by the query string so it resets when filters change.
 */
export function LoadMore<T extends { id: string }>({
  initial,
  query,
  pageSize,
  fetchPage,
  render,
  noun,
}: {
  initial: T[];
  query: QueryParams;
  pageSize: number;
  fetchPage: (params: QueryParams) => Promise<T[]>;
  render: (items: T[]) => ReactNode;
  noun: string;
}) {
  const [items, setItems] = useState(initial);
  const [done, setDone] = useState(initial.length < pageSize);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const more = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await fetchPage({ ...query, limit: pageSize, offset: items.length });
      const seen = new Set(items.map((i) => i.id));
      setItems((xs) => [...xs, ...next.filter((n) => !seen.has(n.id))]);
      if (next.length < pageSize) setDone(true);
    } catch {
      setError("Could not load more — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {render(items)}
      <div className="mt-6 flex items-center justify-between gap-4 border-t border-rule pt-4">
        <span className="tnum font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-3">
          {items.length} {noun} shown{done ? " · end of list" : ""}
        </span>
        {error && <span className="font-mono text-[10.5px] text-accent">{error}</span>}
        {!done && (
          <button
            type="button"
            onClick={() => void more()}
            disabled={busy}
            className="border border-ink px-4 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60"
          >
            {busy ? "Loading…" : `Load ${pageSize} more`}
          </button>
        )}
      </div>
    </>
  );
}
