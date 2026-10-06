import type { Metadata } from "next";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { FilterStrip } from "@/components/feed/FilterStrip";
import { FounderRail } from "@/components/feed/FounderRail";
import { RadarCards, RadarCommands } from "@/components/feed/RadarCards";
import { StrangeSignalDispatch } from "@/components/feed/StrangeSignalDispatch";
import { ThesisShiftList } from "@/components/feed/ThesisShiftList";
import { capitalize, clockTime, counted, joinProse, longDate, spell, themeLabel } from "@/components/feed/prose";
import { ApiError, getRadar } from "@/lib/api";
import { fmtCompact, sourceLabel } from "@/lib/format";
import type { RadarCounts, RadarFeed } from "@/lib/types";

export const metadata: Metadata = { title: "Radar" };

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** One sentence of prose from the overnight counts. */
function deck(c: RadarCounts): string {
  const inView = joinProse(
    [
      c.emerging_companies > 0 && counted(c.emerging_companies, "emerging company", "emerging companies"),
      c.founders > 0 && `${spell(c.founders)} ${c.founders === 1 ? "founder" : "founders"} worth watching`,
      c.strange_signals > 0 && counted(c.strange_signals, "unusual technical signal"),
      c.thesis_shifts > 0 && counted(c.thesis_shifts, "thesis shift"),
    ].filter((x): x is string => Boolean(x)),
  );
  const lead =
    c.signals_24h > 0
      ? `${capitalize(counted(c.signals_24h, "new signal"))} overnight`
      : "A quiet night — no new signals in the last 24 hours";
  return inView ? `${lead}: ${inView}.` : `${lead}.`;
}

function Freshness({ feed }: { feed: RadarFeed }) {
  const sources = Object.entries(feed.data_freshness)
    .filter(([, at]) => at)
    .sort(([a], [b]) => a.localeCompare(b));
  return (
    <p className="font-mono text-[10.5px] uppercase leading-relaxed tracking-[0.08em] text-ink-3">
      {feed.snapshot_note && <span className="mr-3 text-ink-2">{feed.snapshot_note}</span>}
      <span className="mr-3 whitespace-nowrap">Compiled {clockTime(feed.generated_at)}</span>
      {sources.length > 0 ? (
        <span>
          <span className="whitespace-nowrap">Last ingest —</span>{" "}
          {sources.map(([k, at], i) => (
            <span key={k} className="whitespace-nowrap">
              {i > 0 && " · "}
              {sourceLabel(k)} {clockTime(at)}
            </span>
          ))}
        </span>
      ) : (
        <span>No completed ingestion runs recorded</span>
      )}
      {feed.demo_mode && <span className="ml-3 whitespace-nowrap text-ink-2">Demo mode · fictional entities labeled</span>}
    </p>
  );
}

function BadFilter({ message }: { message: string }) {
  return (
    <EmptyState
      title="That filter is not on the map."
      action={
        <Link href="/" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
          ← Back to the full radar
        </Link>
      }
    >
      {message}
    </EmptyState>
  );
}

export default async function RadarPage({ searchParams }: PageProps<"/">) {
  const sp = await searchParams;
  const newOnly = first(sp.new) === "1";
  const theme = first(sp.theme)?.trim() || null;

  let feed: RadarFeed;
  try {
    feed = await getRadar({ new_only: newOnly || undefined, theme: theme ?? undefined });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
      return (
        <div className="pb-24">
          <FilterStrip newOnly={newOnly} theme={theme} newThisWeek={null} />
          <div className="mt-10">
            <BadFilter message={err.detail} />
          </div>
        </div>
      );
    }
    throw err;
  }

  const c = feed.counts;
  const filterName = [newOnly && "first seen this week", theme && `in ${themeLabel(theme)}`].filter(Boolean).join(" ");
  const filtered = Boolean(filterName);

  return (
    <div className="pb-24">
      <RadarCommands />

      {/* ── header band ── */}
      <header className="grid gap-x-12 gap-y-6 pb-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="min-w-0">
          <p className="label mb-3 text-ink">Morning intelligence</p>
          <h1 className="font-serif text-[52px] leading-[0.98] tracking-[-0.02em] text-ink md:text-[64px]">
            <time dateTime={feed.date}>{longDate(feed.date)}</time>
          </h1>
          <p className="mt-4 max-w-[60ch] font-serif text-[20px] italic leading-snug text-ink-2 md:text-[22px]">{deck(c)}</p>
          <div className="mt-4">
            <Freshness feed={feed} />
          </div>
        </div>
        <dl className="flex gap-10 lg:pb-[30px]">
          <div className="border-t border-ink pt-2.5">
            <dt className="label">Signals · 24h</dt>
            <dd className="tnum mt-1 font-serif text-[44px] leading-none tracking-[-0.02em] text-accent">{fmtCompact(c.signals_24h)}</dd>
          </div>
          <div className="border-t border-ink pt-2.5">
            <dt className="label">New · 7 days</dt>
            <dd className="tnum mt-1 font-serif text-[44px] leading-none tracking-[-0.02em] text-ink">{fmtCompact(c.new_entities_7d)}</dd>
          </div>
        </dl>
      </header>

      <FilterStrip newOnly={newOnly} theme={theme} newThisWeek={c.new_entities_7d} />

      <div className="mt-10 grid gap-x-14 gap-y-16 xl:grid-cols-[minmax(0,1fr)_316px]">
        {/* ── main column ── */}
        <section aria-labelledby="radar-cards" className="min-w-0">
          <SectionLabel
            index={1}
            id="radar-cards"
            right={
              <span className="tnum">
                {feed.cards.length} {feed.cards.length === 1 ? "company" : "companies"} · ranked by Anti Score
              </span>
            }
          >
            {filtered ? `Radar — ${filterName}` : "Today’s radar"}
          </SectionLabel>
          <p className="mb-8 mt-2 font-mono text-[10.5px] text-ink-3">
            J / K to move · O open · Y why · W watch · I investigate
          </p>
          <RadarCards
            cards={feed.cards}
            emptyTitle={filtered ? `Nothing ${filterName} cleared the bar today.` : "No companies cleared the bar today."}
            emptyBody={
              filtered
                ? "The radar only shows companies with a scored, explainable signal. Try another filter or the full radar."
                : "No company has a current Anti Score with supporting evidence. Check provider status for ingestion gaps."
            }
          />
        </section>

        {/* ── right rail ── */}
        <aside aria-label="Rail" className="min-w-0 xl:sticky xl:top-6 xl:max-h-[calc(100vh-3rem)] xl:self-start xl:overflow-y-auto xl:pr-1 [scrollbar-width:thin]">
          <div className="grid gap-x-10 gap-y-12 md:grid-cols-2 xl:grid-cols-1">
            <section aria-labelledby="rail-founders">
              <SectionLabel index={2} id="rail-founders" as="h2" right={<span className="tnum">{feed.founders.length}</span>}>
                Founders worth watching
              </SectionLabel>
              <div className="mt-4">
                {feed.founders.length > 0 ? (
                  <>
                    <FounderRail founders={feed.founders.slice(0, 6)} />
                    <Link href="/founders" className="label mt-3 inline-block text-ink hover:text-accent">
                      {feed.founders.length > 6 ? `${feed.founders.length - 6} more · ` : ""}All founders →
                    </Link>
                  </>
                ) : (
                  <p className="text-[13px] leading-snug text-ink-3">No founder has a scored signal {filtered ? filterName : "today"}.</p>
                )}
              </div>
            </section>

            <section aria-labelledby="rail-strange" className="md:row-span-2 xl:row-span-1">
              <SectionLabel index={3} id="rail-strange" right={<span className="tnum">{feed.strange_signals.length}</span>}>
                Strange signals
              </SectionLabel>
              <div className="mt-4 space-y-6">
                {feed.strange_signals.length > 0 ? (
                  feed.strange_signals.map((s, i) => (
                    <StrangeSignalDispatch key={s.id} signal={s} index={i + 1} className={i > 0 ? "border-t border-rule pt-5" : undefined} />
                  ))
                ) : (
                  <p className="text-[13px] leading-snug text-ink-3">
                    No unusual patterns detected{filtered ? ` ${filterName}` : ""}. Detectors look for quiet velocity, lab clusters and
                    shared-employer formations.
                  </p>
                )}
              </div>
            </section>

            <section aria-labelledby="rail-thesis">
              <SectionLabel
                index={4}
                id="rail-thesis"
                right={
                  c.thesis_shifts > feed.thesis_shifts.length ? (
                    <span className="tnum">
                      {feed.thesis_shifts.length} of {c.thesis_shifts}
                    </span>
                  ) : (
                    <span className="tnum">{feed.thesis_shifts.length}</span>
                  )
                }
              >
                Thesis shifts
              </SectionLabel>
              <div className="mt-4">
                {feed.thesis_shifts.length > 0 ? (
                  <ThesisShiftList shifts={feed.thesis_shifts} />
                ) : (
                  <p className="text-[13px] leading-snug text-ink-3">No thesis area moved enough this week to call a shift.</p>
                )}
                <Link href="/thesis" className="label mt-3 inline-block text-ink hover:text-accent">
                  Full thesis map →
                </Link>
              </div>
            </section>
          </div>
        </aside>
      </div>
    </div>
  );
}
