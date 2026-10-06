import type { Metadata } from "next";
import { PageHeader } from "@/components/insight/PageHeader";
import { WatchlistBoard, type TopicMetrics } from "@/components/insight/watchlist/WatchlistBoard";
import { getThesis, getWatchlist } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { ThesisMap } from "@/lib/types";

export const metadata: Metadata = { title: "Watchlist" };

export default async function WatchlistPage() {
  const [wl, thesis] = await Promise.all([getWatchlist(), getThesis().catch((): ThesisMap | null => null)]);

  const topics: Record<string, TopicMetrics> = {};
  for (const t of thesis?.themes ?? [])
    for (const c of t.cells)
      topics[c.key] = {
        label: c.label,
        theme: t.label,
        momentum: c.momentum,
        momentum_trend: c.momentum_trend,
        attention: c.companies > 0 ? c.attention : null,
        attention_gap: c.attention_gap,
        emerging_companies: c.emerging_companies,
        papers_30d: c.papers_30d,
      };

  const n = wl.changes_since_last_visit;
  const lastSeen =
    wl.items
      .map((i) => i.last_seen_at)
      .sort()
      .at(-1) ?? null;
  const changedNames = wl.items.filter((i) => i.changes.length).map((i) => i.name);
  const boardKey = wl.items.map((i) => `${i.id}:${i.changes.length}:${i.last_seen_at}`).join("|");

  return (
    <div>
      <PageHeader
        kicker={<>Watchlist · {wl.name}</>}
        title={
          wl.items.length === 0 ? (
            <>Nothing watched yet.</>
          ) : n > 0 ? (
            <>
              <span className="tnum text-accent">{n}</span> {n === 1 ? "change" : "changes"} since your last visit.
            </>
          ) : (
            <>Nothing has changed since your last visit.</>
          )
        }
        dek={
          wl.items.length === 0 ? (
            "Track companies, founders and thesis topics. Anything that moves — a new repository, a hiring burst, an affiliation change, a score jump — is listed here the next morning."
          ) : (
            <>
              {changedNames.length > 0 ? (
                <>
                  Movement on {changedNames.slice(0, 3).join(", ")}
                  {changedNames.length > 3 && ` and ${changedNames.length - 3} more`}.{" "}
                </>
              ) : (
                <>All {wl.items.length} watched items are quiet. </>
              )}
              {lastSeen && <>Last marked seen {fmtDateTime(lastSeen)}.</>}
            </>
          )
        }
      />
      <WatchlistBoard key={boardKey} items={wl.items} changesSinceLastVisit={n} topics={topics} />
    </div>
  );
}
