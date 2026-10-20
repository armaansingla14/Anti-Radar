import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { EmptyState } from "@/components/EmptyState";
import { isoWeekFor, meetDeck, parseWeekParam, weekOptions, weekRange, weekShort } from "@/components/meet/logic";
import { MeetRow } from "@/components/meet/MeetRow";
import { RefreshButton } from "@/components/meet/RefreshButton";
import { ApiError, getMeet, getWatchlist } from "@/lib/api";
import { today } from "@/lib/clock";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import type { MeetFeed } from "@/lib/types";

export const metadata: Metadata = { title: "Who to meet this week" };

async function watchedPeople(): Promise<Set<string>> {
  try {
    const wl = await getWatchlist();
    return new Set(wl.items.filter((i) => i.entity_type === "person").map((i) => i.entity_id));
  } catch {
    return new Set();
  }
}

export default async function MeetPage({ searchParams }: PageProps<"/meet">) {
  const requested = parseWeekParam((await searchParams).week);

  let feed: MeetFeed | null = null;
  let missing: string | null = null;
  try {
    feed = await getMeet(requested);
  } catch (e) {
    if (!(e instanceof ApiError && (e.status === 404 || e.status === 422))) throw e;
    missing =
      e.status === 404
        ? requested === "current"
          ? "This week's list has not been computed yet — it is ranked after the daily scoring run."
          : `No recommendations were stored for ${weekShort(requested)}.`
        : e.detail;
  }
  const [session, watched] = await Promise.all([getSession(), watchedPeople()]);
  const readOnly = session.read_only;

  const currentWeek = requested === "current" && feed ? feed.week : isoWeekFor(today());
  const options = feed ? weekOptions(feed, requested, currentWeek) : [];
  const isCurrent = feed?.week === currentWeek;
  const people = feed?.people ?? [];
  const range = feed ? weekRange(feed.week) : null;

  return (
    <div className="pb-24">
      <header className="grid gap-x-12 gap-y-6 pb-8 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="min-w-0">
          <p className="label mb-3 text-accent">{isCurrent || !feed ? "This week" : `Archive · ${weekShort(feed.week)}`}</p>
          <h1 className="max-w-[17ch] font-serif text-[46px] leading-[1.0] tracking-[-0.02em] text-ink md:text-[58px]">
            {isCurrent || !feed ? "Who should we meet this week?" : "Who we flagged to meet that week."}
          </h1>
          <p className="mt-4 max-w-[60ch] font-serif text-[20px] italic leading-snug text-ink-2 md:text-[22px]">
            {feed ? meetDeck(people.length) : "The weekly list is not available."}
          </p>
          {feed && (
            <p className="mt-4 font-mono text-[10.5px] uppercase leading-relaxed tracking-[0.08em] text-ink-3">
              <span className="text-ink-2">
                {weekShort(feed.week)}
                {range && ` · ${range}`}
              </span>
              {feed.as_of && <span className="ml-3">Data as of {fmtDate(feed.as_of)}</span>}
              {feed.generated_at && <span className="ml-3">Ranked {fmtDateTime(feed.generated_at)}</span>}
              <span className="ml-3">Deterministic ranking · no language model</span>
            </p>
          )}
        </div>
        {feed && people.length > 0 && (
          <dl className="flex gap-8 lg:pb-[30px]">
            {(["HIGH", "MEDIUM", "WATCH"] as const).map((p) => {
              const n = people.filter((c) => c.priority.toUpperCase() === p).length;
              return (
                <div key={p} className="border-t border-ink pt-2.5">
                  <dt className="label">{p === "WATCH" ? "Watch" : `${p[0]}${p.slice(1).toLowerCase()}`}</dt>
                  <dd className={clsx("tnum mt-1 font-serif text-[40px] leading-none tracking-[-0.02em]", p === "HIGH" && n > 0 ? "text-accent" : "text-ink")}>{n}</dd>
                </div>
              );
            })}
          </dl>
        )}
      </header>

      {options.length > 0 && (
        <nav aria-label="Week" className="flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-rule py-2.5">
          <span className="label text-ink-3">Week</span>
          {options.map((o) => (
            <Link
              key={o.week}
              href={o.href}
              aria-current={o.selected ? "page" : undefined}
              className={clsx(
                "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors",
                o.selected ? "border-b border-accent text-ink" : "text-ink-2 hover:text-ink",
              )}
              title={weekRange(o.week) ?? o.week}
            >
              {o.label}
            </Link>
          ))}
          {!readOnly && isCurrent && (
            <span className="ml-auto">
              <RefreshButton />
            </span>
          )}
        </nav>
      )}

      <p className="mt-3 max-w-[104ch] text-[12.5px] leading-relaxed text-ink-3">
        Ranked by founder score, formation evidence, thesis fit, recent dated change and attention gap — with a penalty for people who
        are already widely known. Dismissed people never return. Affiliations from GitHub profiles are self-reported; public org
        membership is verified membership, not employment. Nobody is called a founder without a founder relation on record.
        <span className="ml-2 font-mono text-[10.5px]">J / K move · O open · W watch</span>
      </p>

      {missing && (
        <div className="mt-10">
          <EmptyState
            title={requested === "current" ? "No list for this week yet." : "No list for that week."}
            action={
              requested !== "current" && (
                <Link href="/meet" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                  ← This week
                </Link>
              )
            }
          >
            {missing}
          </EmptyState>
        </div>
      )}

      {feed && people.length === 0 && (
        <div className="mt-10">
          <EmptyState
            title="No one stands out this week — the radar needs more evidence."
            action={
              <>
                <Link href="/formation" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                  Formation radar →
                </Link>
                <Link href="/founders?pre=1" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                  People with no company on record →
                </Link>
              </>
            }
          >
            A person is recommended only when dated public evidence changed recently — a new repository or org, a paper, a
            commit burst, a formation signal. We would rather show nobody than pad the list with famous names.
          </EmptyState>
        </div>
      )}

      {people.length > 0 && (
        <ol className="mt-10" aria-label={`Recommendations for ${feed?.week}`}>
          {people.map((card) => (
            <li key={card.person.id}>
              <MeetRow card={card} readOnly={readOnly} watched={watched.has(card.person.id)} />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
