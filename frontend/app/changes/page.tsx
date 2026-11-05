import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import type { ReactNode } from "react";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { changesTitle, dayLabel, hasReport, humanizeDays, parseRange, rangeLine, SECTION_IDS as S } from "@/components/changes/logic";
import {
  Caveats,
  CollisionsBlock,
  FormationBlock,
  MeetBlock,
  MomentumBlock,
  NewSection,
  ScoreChangesBlock,
  SignalsByFamily,
} from "@/components/changes/Sections";
import { ApiError, getChanges } from "@/lib/api";
import { loadDiligenceIndex } from "@/components/entity/data";
import { workspaceMap } from "@/components/diligence/logic";
import type { ChangesReport } from "@/lib/types-changes";

export const metadata: Metadata = { title: "What changed" };

const back = (
  <Link href="/" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
    ← Radar
  </Link>
);

/** Other comparisons when more than two snapshot days were replayed (?base=&current=). */
function RangePicker({ report }: { report: ChangesReport }) {
  const pts = report.points;
  if (pts.length <= 2) return null;
  const latest = pts[pts.length - 1];
  return (
    <p className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">
      <span>Compare {dayLabel(latest)} with</span>
      {pts.slice(0, -1).map((d) => (
        <Link
          key={d}
          href={`/changes?base=${d}&current=${latest}`}
          className={clsx("link", d === report.base_day && report.current_day === latest ? "text-ink" : "text-ink-2")}
        >
          {dayLabel(d)}
        </Link>
      ))}
    </p>
  );
}

export default async function ChangesPage({ searchParams }: PageProps<"/changes">) {
  const range = parseRange(await searchParams);
  const diligenceP = loadDiligenceIndex(); // "Open diligence" on score rows: one fetch for the page
  let report: ChangesReport | null = null;
  let rejected: string | null = null;
  try {
    report = await getChanges(range);
  } catch (e) {
    if (e instanceof ApiError && e.status === 422) rejected = e.detail;
    else if (!(e instanceof ApiError && e.status === 404)) throw e;
  }

  const unavailable = report && !report.available ? report.reason : null;
  if (!hasReport(report)) {
    return (
      <div className="pb-24">
        <PageHeader kicker="Since last snapshot" title="What changed" />
        <EmptyState title={rejected ? "That comparison is not available." : "Only one snapshot so far."} action={back}>
          {rejected
            ? humanizeDays(rejected)
            : unavailable
              ? humanizeDays(unavailable)
              : "A change report needs two verified captures of the same sources. It appears once the next dated update is captured."}
        </EmptyState>
      </div>
    );
  }

  const r = report;
  const diligence = new Set(workspaceMap(await diligenceP).keys());
  const s = r.score_changes;
  const contents: [string, string, number | null][] = [
    [S.companies, "New companies", r.new_companies.count],
    [S.people, "New people", r.new_people.count],
    [S.signals, "Signals", r.new_signals_total],
    [S.scores, "Score moves", s.comparable ? s.accelerated.count + s.weakened.count : null],
    [S.momentum, "Momentum", r.momentum_acceleration.count],
    [S.collisions, "Collisions", r.new_collisions.count + r.collisions_ended.count],
    [S.formation, "Formation", r.formation.available ? Object.values(r.formation.counts).reduce((a, b) => a + (b ?? 0), 0) - (r.formation.counts.UNCHANGED ?? 0) : null],
    [S.meet, "Who to meet", r.meet.available ? r.meet.entered.length + r.meet.left.length + r.meet.moved.length : null],
  ];
  let idx = 0;
  const sec = (id: string, title: string, right: ReactNode, body: ReactNode) => (
    <section key={id} id={id} aria-labelledby={`${id}-h`} className="scroll-mt-24">
      <SectionLabel index={++idx} id={`${id}-h`} right={right}>
        {title}
      </SectionLabel>
      <div className="mt-5">{body}</div>
    </section>
  );

  return (
    <div className="pb-24">
      <PageHeader
        kicker={<span className="text-ink">Since last snapshot · {rangeLine(r)}</span>}
        title={changesTitle(r)}
        dek={
          <>
            Two dated captures of the same sources, compared. <em>New</em> means new to Radar — first observed in the later capture — not
            newly created; creation and publication dates sit next to each item. Score moves compare like with like only.
            <RangePicker report={r} />
          </>
        }
        aside={
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <div className="border-t border-ink pt-2">
              <dt className="label">From</dt>
              <dd className="mt-1 font-serif text-[24px] leading-none text-ink">{dayLabel(r.base_day)}</dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label">To</dt>
              <dd className="mt-1 font-serif text-[24px] leading-none text-ink">{dayLabel(r.current_day)}</dd>
            </div>
            <div className="border-t border-rule pt-2">
              <dt className="label">New signals</dt>
              <dd className="tnum mt-1 font-serif text-[24px] leading-none text-ink">{r.new_signals_total.toLocaleString("en-US")}</dd>
            </div>
            <div className="border-t border-rule pt-2">
              <dt className="label">Companies compared</dt>
              <dd className="tnum mt-1 font-serif text-[24px] leading-none text-ink">{s.comparable ? s.compared.toLocaleString("en-US") : "—"}</dd>
            </div>
          </dl>
        }
      />

      <Caveats caveats={r.caveats} id={S.caveats} />

      <nav aria-label="Sections" className="mt-8 flex flex-wrap gap-x-6 gap-y-2 border-b border-rule pb-3">
        {contents.map(([id, label, n]) => (
          <a key={id} href={`#${id}`} className="label text-ink-2 transition-colors hover:text-accent">
            {label}
            {n !== null && <span className="tnum ml-1.5 text-ink">{n.toLocaleString("en-US")}</span>}
          </a>
        ))}
      </nav>

      <div className="mt-12 space-y-16">
        {sec(
          "new",
          "New to Radar",
          <span>first observed by {dayLabel(r.current_day)}</span>,
          <div className="grid gap-x-12 gap-y-12 lg:grid-cols-2">
            <NewSection id={S.companies} title="New companies" section={r.new_companies} by="by Anti Score" empty="No company was first observed in the later capture." />
            <NewSection id={S.people} title="New people" section={r.new_people} by="by Founder Score" empty="No person was first observed in the later capture." />
            <NewSection id={S.repositories} title="New repositories" section={r.new_repositories} by="by stars" empty="No repository was first observed in the later capture." />
            <NewSection id={S.hiring} title="New hiring" section={r.new_jobs} by="newest first" empty="No new job postings were observed." />
            <NewSection
              id={S.research}
              title="New research by people already on Radar"
              section={r.new_research}
              by="newest first"
              empty="No new papers by people who were already on Radar."
            />
            <NewSection id={S.papers} title="New papers" section={r.new_papers} by="newest first" empty="No paper was first observed in the later capture." />
          </div>,
        )}

        {sec(S.signals, "New signals by source family", <span className="tnum">{r.new_signals_total.toLocaleString("en-US")} total</span>, (
          <SignalsByFamily total={r.new_signals_total} families={r.new_signals_by_family} />
        ))}

        {sec(
          S.scores,
          "Score changes",
          s.comparable ? <span className="tnum">{s.compared} compared</span> : <span>not compared</span>,
          <ScoreChangesBlock report={r} diligence={diligence} />,
        )}

        {sec(S.momentum, "Momentum acceleration", <span className="tnum">{r.momentum_acceleration.count}</span>, <MomentumBlock list={r.momentum_acceleration} />)}

        {sec(
          S.collisions,
          "Collisions",
          <span className="tnum">
            {r.new_collisions.count} new · {r.collisions_ended.count} ended
          </span>,
          <CollisionsBlock report={r} />,
        )}

        {sec(S.formation, "Formation", r.formation.available ? <span>{dayLabel(r.formation.base_day)} → {dayLabel(r.formation.current_day)}</span> : null, (
          <FormationBlock report={r} />
        ))}

        {sec(S.meet, "Who to meet", r.meet.available ? <span>{r.meet.base_week} → {r.meet.current_week}</span> : null, <MeetBlock report={r} />)}
      </div>
    </div>
  );
}
