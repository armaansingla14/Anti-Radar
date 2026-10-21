import type { Metadata } from "next";
import { displayNow } from "@/lib/dataSource";
import clsx from "clsx";
import Link from "next/link";
import { connection } from "next/server";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { Confidence } from "@/components/Confidence";
import { Delta } from "@/components/Delta";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { SourcesButton } from "@/components/SourceDrawer";
import { GeneratorNote } from "@/components/feed/GeneratorNote";
import { longDate } from "@/components/feed/prose";
import { ApiError } from "@/lib/api";
import { DAILY_SECTIONS, type DailyRadarV2, type DailyRow, dailyRows, getDailyV2 } from "@/lib/api-pages";
import { fmtDate, fmtDateTime, pad2 } from "@/lib/format";
import { ageShort } from "@/lib/labels";

export const metadata: Metadata = { title: "Daily" };

const WINDOWS = [
  { key: "", label: "Since last visit" },
  { key: "1", label: "24 hours" },
  { key: "3", label: "3 days" },
  { key: "7", label: "7 days" },
];

function Row({ row, index, now }: { row: DailyRow; index: number; now: Date }) {
  const external = row.href && /^https?:\/\//.test(row.href);
  const title = <span className="font-serif text-[19px] leading-tight text-ink">{row.headline}</span>;
  return (
    <li className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 border-t border-rule py-3.5 sm:grid-cols-[28px_minmax(0,1fr)_200px]">
      <span className="tnum pt-[5px] font-mono text-[10.5px] text-ink-3">{pad2(index)}</span>
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          {row.href ? (
            external ? (
              <a href={row.href} target="_blank" rel="noopener noreferrer nofollow" className="decoration-accent underline-offset-4 hover:underline">
                {title}
                <span aria-hidden className="ml-1 text-[12px] text-ink-3">↗</span>
              </a>
            ) : (
              <Link href={row.href} className="decoration-accent underline-offset-4 hover:underline">
                {title}
              </Link>
            )
          ) : (
            title
          )}
          {row.label && <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink-2">{row.label}</span>}
          {row.isDemo && <DemoBadge />}
        </p>
        {row.whyNow && (
          <p className="mt-1 max-w-[78ch] text-[13.5px] leading-snug text-ink">
            <span className="label mr-2 text-ink-3">Why now</span>
            {row.whyNow}
          </p>
        )}
      </div>
      <div className="col-start-2 mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 sm:col-start-3 sm:mt-0 sm:flex-col sm:items-end sm:gap-y-1.5">
        {row.at ? (
          <time dateTime={row.at} title={fmtDateTime(row.at)} className="tnum font-mono text-[10.5px] uppercase text-ink-2" suppressHydrationWarning>
            {fmtDate(row.at, { year: false })} · {ageShort(row.at, now) ?? ""}
          </time>
        ) : (
          <span className="font-mono text-[10.5px] uppercase text-ink-3">undated</span>
        )}
        <SourcesButton claim={row.headline} evidence={row.evidence} count={row.sources} demo={row.isDemo} />
        <Confidence value={row.confidence} />
        {row.delta !== null && <Delta value={row.delta} period="7d" size="xs" />}
      </div>
    </li>
  );
}

async function loadDaily(since?: string): Promise<DailyRadarV2 | { error: string }> {
  try {
    return await getDailyV2(since ? { since } : undefined);
  } catch (err) {
    if (err instanceof ApiError) return { error: err.status === 404 ? "The Daily Radar v2 endpoint is not available on this backend yet." : err.detail };
    throw err;
  }
}

export default async function DailyPage({ searchParams }: PageProps<"/daily">) {
  await connection();
  const sp = await searchParams;
  const raw = Array.isArray(sp.window) ? sp.window[0] : sp.window;
  const win = WINDOWS.find((w) => w.key && w.key === raw)?.key ?? "";
  const now = displayNow();
  const since = win ? new Date(now.getTime() - Number(win) * 86_400_000).toISOString() : undefined;

  const daily = await loadDaily(since);
  if ("error" in daily) {
    return (
      <EmptyState title="Daily Radar unavailable.">
        {daily.error} Nothing is shown rather than a partial or reconstructed edition.
      </EmptyState>
    );
  }
  const sections = DAILY_SECTIONS.map((s) => ({ ...s, rows: dailyRows(daily[s.key]) }));
  const snapshotNote = daily.is_snapshot ? `Data as of ${longDate(daily.data_as_of)} (snapshot)` : null;

  return (
    <div className="pb-24">
      <header className="grid gap-x-12 gap-y-6 border-b border-ink pb-8 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0">
          <p className="label mb-3 text-ink">Daily radar · {longDate(daily.date)}</p>
          <h1 className="font-serif text-[48px] leading-[1.02] tracking-[-0.018em] text-ink md:text-[58px]">What changed since yesterday?</h1>
          <p className="mt-4 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2">
            Changes since <time dateTime={daily.since}>{fmtDateTime(daily.since)}</time>
            {snapshotNote && <span className="text-accent"> · {snapshotNote}</span>}
          </p>
          <nav aria-label="Time window" className="mt-4 flex flex-wrap gap-x-5 gap-y-2">
            {WINDOWS.map((w) => {
              const active = w.key === win;
              return (
                <Link
                  key={w.key || "last"}
                  href={w.key ? `/daily?window=${w.key}` : "/daily"}
                  aria-current={active ? "true" : undefined}
                  className={clsx(
                    "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150",
                    active ? "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]" : "text-ink-2 hover:text-ink",
                  )}
                >
                  {w.label}
                </Link>
              );
            })}
          </nav>
          <ol aria-label="In this edition" className="mt-8 grid max-w-[700px] grid-cols-2 gap-x-8 border-t border-rule pt-3 sm:grid-cols-3">
            {sections.map((s, i) => (
              <li key={s.key}>
                <a href={`#d-${s.key}`} className="group flex items-baseline gap-2 py-1 text-[13px] text-ink-2 hover:text-ink">
                  <span className="tnum font-mono text-[10.5px] text-ink-3">{pad2(i + 1)}</span>
                  <span className="group-hover:underline group-hover:decoration-accent group-hover:underline-offset-4">{s.title}</span>
                  <span className={clsx("tnum ml-auto font-mono text-[11px]", s.rows.length ? "text-ink" : "text-ink-3")}>{s.rows.length}</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
        <div className="min-w-0 lg:border-l lg:border-rule lg:pl-8">
          <p className="label mb-2 flex items-center gap-2 text-ink">
            The short version {daily.summary && <EpistemicBadge value={daily.summary.generator === "template" ? daily.summary.epistemic : "MODEL_OPINION"} />}
          </p>
          {daily.summary ? (
            <>
              <p className="font-serif text-[17.5px] leading-[1.5] text-ink">{daily.summary.text}</p>
              <GeneratorNote generator={daily.summary.generator} model={daily.summary.model} className="mt-3" />
            </>
          ) : (
            <p className="text-[13.5px] text-ink-3">No summary — nothing on the radar changed enough to narrate.</p>
          )}
          <p className="mt-5 border-t border-rule pt-3 text-[12px] leading-snug text-ink-3">
            Every entry states why now, when it happened, the sources behind it and how broad the evidence is. Headlines are built from
            stored records, not written by a model.
          </p>
        </div>
      </header>

      {sections.map((s, i) => (
        <section key={s.key} aria-labelledby={`d-${s.key}`} className="mt-14">
          <SectionLabel index={i + 1} id={`d-${s.key}`} right={<span className="tnum">{s.rows.length}</span>}>
            {s.title}
          </SectionLabel>
          <div className="mt-4">
            {s.rows.length > 0 ? (
              <ol className="border-b border-rule">
                {s.rows.map((r, j) => (
                  <Row key={r.key} row={r} index={j + 1} now={now} />
                ))}
              </ol>
            ) : s.key === "watchlist_changes" ? (
              <EmptyState
                title="Nothing on your watchlist moved."
                action={
                  <Link href="/watchlist" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                    Open watchlist →
                  </Link>
                }
              >
                Watched companies and founders report score moves, new signals and role changes here. Press W on any card to watch it.
              </EmptyState>
            ) : (
              <p className="border-t border-rule py-5 text-[13.5px] text-ink-3">{s.empty}</p>
            )}
          </div>
        </section>
      ))}

      <p className="mt-12 flex flex-wrap gap-x-6 gap-y-2">
        <Link href="/?new=week" className="label text-ink hover:text-accent">
          Everything Unknown Yesterday →
        </Link>
        <Link href="/brief/today" className="label text-ink hover:text-accent">
          Today&apos;s stored brief →
        </Link>
      </p>
    </div>
  );
}
