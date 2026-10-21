"use client";

import { useStaticSnapshot } from "@/components/StaticSnapshot";
import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { DemoBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { EmptyState } from "@/components/EmptyState";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SectionLabel } from "@/components/SectionLabel";
import { useToast } from "@/components/Toast";
import { markWatchlistSeen, postMemo, removeWatch } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtNum, hrefFor, humanize } from "@/lib/format";
import type { Trend, WatchItemOut } from "@/lib/types";
import { describeApiError } from "../apiError";
import { TrendGlyph } from "../TrendGlyph";

export interface TopicMetrics {
  label: string;
  theme: string;
  momentum: number;
  momentum_trend: Trend;
  attention: number | null;
  attention_gap: boolean;
  emerging_companies: number;
  papers_30d: number;
}

export interface WatchlistBoardProps {
  items: WatchItemOut[];
  changesSinceLastVisit: number;
  topics: Record<string, TopicMetrics>;
}

const GROUPS: { type: string; title: string; empty: string }[] = [
  { type: "company", title: "Companies", empty: "No companies watched." },
  { type: "person", title: "Founders", empty: "No founders watched." },
  { type: "topic", title: "Thesis topics", empty: "No thesis topics watched." },
  { type: "repository", title: "Repositories", empty: "No repositories watched." },
];

const act =
  "font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-ink disabled:cursor-wait disabled:text-ink-3";

function entityHref(it: WatchItemOut): string {
  if (it.entity_type === "topic") return `/thesis#cell-${it.topic_key ?? it.slug ?? ""}`;
  return hrefFor(it.entity_type, it.slug ?? it.entity_id);
}

function Row({
  it,
  topic,
  onRemove,
  removing,
}: {
  it: WatchItemOut;
  topic?: TopicMetrics;
  onRemove: (it: WatchItemOut) => void;
  removing: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [memoPending, setMemoPending] = useState(false);
  const href = entityHref(it);
  const isEntity = it.entity_type === "company" || it.entity_type === "person";
  const changed = it.changes.length > 0;

  const memo = async () => {
    if (!isEntity || memoPending) return;
    setMemoPending(true);
    try {
      const report = await postMemo({ entity_type: it.entity_type as "company" | "person", entity_id: it.entity_id });
      toast(report.generator === "llm" ? "Memo drafted" : `Memo generated (${report.generator} output)`);
      router.push(`/reports/${encodeURIComponent(report.id)}`);
    } catch (err) {
      toast(`Memo failed — ${describeApiError(err)}`, { tone: "error", durationMs: 5000 });
      setMemoPending(false);
    }
  };

  return (
    <li
      data-card
      data-href={href}
      tabIndex={0}
      aria-label={it.name}
      className={clsx(
        "relative grid grid-cols-12 gap-x-6 gap-y-3 border-b border-rule py-5 pl-4 transition-opacity duration-150",
        removing && "opacity-40",
      )}
    >
      {changed && <span aria-hidden className="absolute inset-y-5 left-0 w-[2px] bg-accent" />}
      <div className="col-span-12 min-w-0 md:col-span-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label">{it.entity_type === "topic" ? (topic?.theme ?? "Topic") : humanize(it.entity_type)}</span>
          {it.is_demo && <DemoBadge />}
          {changed && (
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-accent">● {it.changes.length} new</span>
          )}
        </div>
        <h3 className="mt-1 font-serif text-[23px] leading-tight tracking-[-0.005em] text-ink">
          <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {it.name}
          </Link>
        </h3>
        <p className="mt-1 font-mono text-[10.5px] text-ink-3">
          Watching since {fmtDate(it.created_at)}
          {it.note && <span className="ml-2 font-sans text-[12px] italic text-ink-2">“{it.note}”</span>}
        </p>
      </div>

      <div className="col-span-12 min-w-0 md:col-span-4">
        {changed ? (
          <ul className="space-y-1.5">
            {it.changes.map((c, i) => (
              <li key={i} className="grid grid-cols-[16px_1fr] text-[13.5px] leading-snug text-ink">
                <span aria-hidden className="text-accent">
                  →
                </span>
                {c}
              </li>
            ))}
          </ul>
        ) : it.entity_type === "topic" && topic ? (
          <p className="text-[13px] leading-snug text-ink-2">
            {topic.emerging_companies} emerging {topic.emerging_companies === 1 ? "company" : "companies"} · {topic.papers_30d}{" "}
            papers in 30 days
            {topic.attention_gap && (
              <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.08em] text-accent">◆ Possible attention gap</span>
            )}
          </p>
        ) : (
          <p className="text-[13px] text-ink-3">No changes since {fmtDateTime(it.last_seen_at)}.</p>
        )}
      </div>

      <div className="col-span-6 flex items-start gap-4 md:col-span-1 md:justify-end">
        {it.entity_type === "topic" ? (
          topic ? (
            <div className="text-right" title="Topic momentum (thesis map)">
              <span className="label block text-[9.5px]">Momentum</span>
              <span className="tnum font-serif text-[22px] leading-none text-ink">{fmtNum(topic.momentum, 0)}</span>
              <TrendGlyph trend={topic.momentum_trend} className="ml-1 text-[11px]" />
            </div>
          ) : (
            <span className="font-mono text-[10.5px] text-ink-3">—</span>
          )
        ) : (
          <div className="flex flex-col items-end gap-1">
            <ScoreNumeral
              value={it.score}
              size="sm"
              label={it.entity_type === "person" ? "Founder" : "Anti"}
              className="items-end"
            />
            <Delta value={it.delta_7d} period="7d" size="xs" />
          </div>
        )}
      </div>

      <div className="col-span-6 flex flex-wrap items-start justify-end gap-x-4 gap-y-2 pt-4 md:col-span-3">
        <Link href={href} data-action="open" className={act}>
          Open
        </Link>
        {isEntity && (
          <>
            <Link href={`${href}?investigate=1`} data-action="investigate" className={act}>
              Investigate
            </Link>
            <button type="button" onClick={memo} disabled={memoPending} aria-busy={memoPending} className={act}>
              {memoPending ? "Writing memo…" : "Memo"}
            </button>
          </>
        )}
        {it.entity_type === "topic" && (
          <Link href={`/search?q=${encodeURIComponent(it.name)}`} className={act}>
            Search
          </Link>
        )}
        <button
          type="button"
          data-action="watch"
          onClick={() => onRemove(it)}
          disabled={removing}
          className={clsx(act, "hover:text-accent")}
        >
          Remove
        </button>
      </div>
    </li>
  );
}

/** Interactive watchlist: grouped rows, change lines, memo/investigate/remove and mark-seen. */
export function WatchlistBoard({ items: initial, changesSinceLastVisit, topics }: WatchlistBoardProps) {
  const router = useRouter();
  const toast = useToast();
  const staticSnapshot = useStaticSnapshot();
  const [items, setItems] = useState(initial);
  const [removing, setRemoving] = useState<Set<string>>(new Set());
  const [seenPending, setSeenPending] = useState(false);
  const [refreshing, startRefresh] = useTransition();

  const remove = async (it: WatchItemOut) => {
    setRemoving((s) => new Set(s).add(it.id));
    try {
      await removeWatch(it.id);
      setItems((xs) => xs.filter((x) => x.id !== it.id));
      toast(`Removed ${it.name} from the watchlist`);
      startRefresh(() => router.refresh());
    } catch (err) {
      toast(`Could not remove — ${describeApiError(err)}`, { tone: "error" });
    } finally {
      setRemoving((s) => {
        const n = new Set(s);
        n.delete(it.id);
        return n;
      });
    }
  };

  const markSeen = async () => {
    setSeenPending(true);
    try {
      await markWatchlistSeen();
      toast("Marked all as seen");
      startRefresh(() => router.refresh());
    } catch (err) {
      toast(`Could not mark as seen — ${describeApiError(err)}`, { tone: "error" });
    } finally {
      setSeenPending(false);
    }
  };

  if (items.length === 0 && staticSnapshot) {
    return (
      <EmptyState
        title="Watchlists are personal — empty in the verified snapshot."
        action={
          <Link href="/" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
            Open the radar →
          </Link>
        }
      >
        On a live backend, watching a company, person or thesis topic collects its changes here between visits: new roles,
        repositories, papers, affiliation moves and score jumps.
      </EmptyState>
    );
  }
  if (items.length === 0) {
    return (
      <EmptyState
        title="You are not watching anything yet."
        action={
          <>
            <Link href="/" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
              Open the radar →
            </Link>
            <Link href="/thesis" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
              Watch a thesis topic →
            </Link>
          </>
        }
      >
        Press <kbd className="border border-rule-strong px-1 font-mono text-[11px] text-ink">W</kbd> on any focused card (move
        with <kbd className="border border-rule-strong px-1 font-mono text-[11px] text-ink">J</kbd>/
        <kbd className="border border-rule-strong px-1 font-mono text-[11px] text-ink">K</kbd>), or use “+ Watch” on a company,
        founder or thesis cell. Changes — new roles, repos, papers, affiliation moves, score jumps — collect here between visits.
      </EmptyState>
    );
  }

  const groups = GROUPS.map((g) => ({ ...g, rows: items.filter((i) => i.entity_type === g.type) })).filter(
    (g) => g.rows.length > 0,
  );
  const other = items.filter((i) => !GROUPS.some((g) => g.type === i.entity_type));
  if (other.length) groups.push({ type: "other", title: "Other", empty: "", rows: other });

  return (
    <div>
      <div className="mb-10 flex flex-wrap items-center justify-between gap-4 border-y border-ink py-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
          {items.length} watched ·{" "}
          <span className={changesSinceLastVisit ? "text-accent" : undefined}>
            {changesSinceLastVisit} {changesSinceLastVisit === 1 ? "change" : "changes"} since last visit
          </span>
          {refreshing && <span className="ml-3 text-ink-3">refreshing…</span>}
        </p>
        {changesSinceLastVisit > 0 && (
          <button
            type="button"
            onClick={markSeen}
            disabled={seenPending}
            aria-busy={seenPending}
            className="border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60"
            title="Reset the ‘since last visit’ marker to now"
          >
            {seenPending ? "Marking…" : "Mark all as seen"}
          </button>
        )}
      </div>
      {groups.map((g, gi) => (
        <section key={g.type} className="mb-10" aria-labelledby={`wl-${g.type}`}>
          <SectionLabel
            index={gi + 1}
            id={`wl-${g.type}`}
            right={`${g.rows.length} · ${g.rows.filter((r) => r.changes.length).length} changed`}
          >
            {g.title}
          </SectionLabel>
          <ul className="mt-2">
            {[...g.rows]
              .sort((a, b) => b.changes.length - a.changes.length || (b.score ?? -1) - (a.score ?? -1))
              .map((it) => (
                <Row
                  key={it.id}
                  it={it}
                  topic={it.topic_key ? topics[it.topic_key] : undefined}
                  onRemove={remove}
                  removing={removing.has(it.id)}
                />
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
