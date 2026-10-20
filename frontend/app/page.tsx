import type { Metadata } from "next";
import { RadarCommands } from "@/components/feed/RadarCards";
import { FilterStrip } from "@/components/feed/FilterStrip";
import { CompanyRows, FormationRows, HomeCounts, HomeSection, PeopleRows, StrangeRows, WhatChanged } from "@/components/feed/Home";
import { composeHomeFallback, homeStatusLine, parseWindow } from "@/components/feed/homeLogic";
import { longDate } from "@/components/feed/prose";
import { ApiError, getFormation, getHome, getMeet, getRadar } from "@/lib/api";
import type { HomeFeed, HomeWindow } from "@/lib/types";

export const metadata: Metadata = { title: "Radar" };

/** GET /api/home; on an older backend without it (404), compose the same shape from the legacy feeds. */
async function loadHome(w: HomeWindow | null): Promise<{ home: HomeFeed; composed: boolean }> {
  try {
    return { home: await getHome(w ? { new: w } : undefined), composed: false };
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
  }
  const soft = <T,>(p: Promise<T>) => p.catch(() => null);
  const [radar, formation, meet] = await Promise.all([getRadar(), soft(getFormation()), soft(getMeet())]);
  return { home: composeHomeFallback(radar, formation, meet, w), composed: true };
}

const WINDOW_NAME: Record<HomeWindow, string> = { today: "first seen today", week: "first seen this week" };

export default async function HomePage({ searchParams }: PageProps<"/">) {
  const sp = await searchParams;
  const w = parseWindow(sp.new);
  const { home, composed } = await loadHome(w);
  const s = home.summary;
  const now = new Date();
  const status = homeStatusLine(s, now);
  const topRanked = home.companies_basis === "top_ranked";
  const scope = w ? ` · ${WINDOW_NAME[w]}` : "";

  return (
    <div className="pb-20">
      <RadarCommands />

      {/* ── header ── */}
      <header className="flex flex-wrap items-end justify-between gap-x-10 gap-y-2 pb-5">
        <div className="min-w-0">
          <p className="label mb-2 text-ink">Morning intelligence</p>
          <h1 className="font-serif text-[40px] leading-none tracking-[-0.02em] text-ink md:text-[46px]">
            {s.data_as_of ? <time dateTime={s.data_as_of}>{longDate(s.data_as_of)}</time> : "Today"}
          </h1>
        </div>
        <p
          className={
            s.is_snapshot
              ? "font-mono text-[11px] uppercase tracking-[0.08em] text-ink"
              : "font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2"
          }
          suppressHydrationWarning
        >
          {s.is_snapshot && <span aria-hidden className="mr-2 inline-block size-[6px] translate-y-[-1px] bg-accent" />}
          {status}
          {composed && <span className="ml-3 normal-case tracking-normal text-ink-3">· composed from radar, formation and meet feeds</span>}
        </p>
      </header>

      <HomeCounts summary={s} />

      <div className="mt-7">
        <FilterStrip window={w} right="J / K to move · O open" />
      </div>

      <div className="mt-7 grid gap-x-10 gap-y-10 xl:gap-x-12 lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[minmax(0,1fr)_350px]">
        <HomeSection id="what-changed" title={`What changed${scope}`} count={home.what_changed.length}>
          <WhatChanged items={home.what_changed} now={now} />
        </HomeSection>

        <div className="grid min-w-0 content-start gap-x-10 gap-y-10 md:grid-cols-2 lg:grid-cols-1">
          <HomeSection id="people" title="People to meet" count={s.people_to_meet} href="/meet" hrefLabel="This week">
            <PeopleRows people={home.people_to_meet} />
          </HomeSection>
          <HomeSection id="strange" title="Strange signals" count={s.strange_signals}>
            <StrangeRows signals={home.strange_signals} />
          </HomeSection>
        </div>
      </div>

      <div className="mt-10 grid gap-x-10 gap-y-10 xl:gap-x-12 lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[minmax(0,1fr)_350px]">
        <HomeSection
          id="companies"
          title={topRanked ? "Top ranked companies" : "Companies accelerating"}
          count={topRanked ? home.companies.length : s.companies_accelerating}
          href={topRanked ? "/companies" : "/companies?sort=delta"}
          hrefLabel="All companies"
        >
          <CompanyRows cards={home.companies} note={home.companies_note} />
        </HomeSection>
        <HomeSection id="formations" title="Possible formations" count={s.possible_formations} href="/formation" hrefLabel="Formation radar">
          <FormationRows cards={home.formations} stacked />
        </HomeSection>
      </div>
    </div>
  );
}
