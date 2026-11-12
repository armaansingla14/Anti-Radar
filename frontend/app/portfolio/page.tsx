import type { Metadata } from "next";
import clsx from "clsx";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { Section } from "@/components/entity/primitives";
import { loadDiligenceIndex, loadPortfolio, loadPortfolioComps, loadPortfolioIndex } from "@/components/entity/data";
import { canWrite, workspaceMap } from "@/components/diligence/logic";
import { DiligenceHandoff } from "@/components/portfolio/Handoff";
import { dataSource, displayNow } from "@/lib/dataSource";
import { getSession } from "@/lib/session";
import { AttentionCardView, EventList, Ext, FunnelLine, MethodNote, MetricRow, MonitoringBlock } from "@/components/portfolio/Views";
import { compIndex, countsRow, portfolioHref, portfolioSlugs, positionFacts, sinceRows } from "@/components/portfolio/logic";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { updatedAgo } from "@/lib/labels";

export const metadata: Metadata = { title: "Portfolio Pulse" };

export default async function PortfolioPulsePage() {
  const [pulse, index, maps, dIndex, session] = await Promise.all([
    loadPortfolio(),
    loadPortfolioIndex(),
    loadPortfolioComps(),
    loadDiligenceIndex(),
    getSession(),
  ]);
  const writable = canWrite(session, dataSource() === "snapshot");
  const workspaces = workspaceMap(dIndex);
  const now = displayNow();
  const portfolio = portfolioSlugs(index);
  for (const c of pulse.needs_attention) portfolio.add(c.company.slug);
  const comps = compIndex(maps);
  const stages = new Map((index ?? []).map((i) => {
    const f = positionFacts(i.position);
    return [i.company.slug, `${f.stage} · ${f.year} · per antifund.com`];
  }));
  const counts = countsRow(pulse.counts);
  const primary = counts.slice(0, 3);
  const byType = counts.slice(3);
  const f = pulse.funnel;
  const m = pulse.monitoring;
  const window = f.window ? `${fmtDate(f.window.start, { year: false })} → ${fmtDate(f.window.end)}` : null;
  const watchGap = f.available ? f.watch - (pulse.counts.watch ?? 0) : 0;
  const matGap = f.available ? f.material - (pulse.counts.material ?? 0) : 0;
  const rows = sinceRows(index, pulse.since_last_review);
  const relevant = (pulse.counts.material ?? 0) + (pulse.counts.watch ?? 0);

  return (
    <div className="pb-24">
      <header className="grid gap-x-10 gap-y-4 border-b border-ink pb-6 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <p className="label mb-3 text-ink">
            <span aria-hidden className="mr-2 inline-block size-[6px] translate-y-[-1px] bg-accent" />
            Portfolio Pulse
          </p>
          <h1 className="font-serif text-[46px] leading-[1.02] tracking-[-0.018em] text-ink max-md:text-[36px]">What changed after we cared</h1>
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2" suppressHydrationWarning>
            <span title={m.events_updated_at ? fmtDateTime(m.events_updated_at) : undefined}>{updatedAgo(m.events_updated_at, now)}</span>
            {m.data_as_of && <> · data as of {fmtDate(m.data_as_of)}</>}
            {window && <> · 30-day window {window}</>}
          </p>
          <p className="mt-2 text-[13px] leading-snug text-ink-3">
            {m.portfolio_companies} selected publicly disclosed Anti Fund portfolio companies (
            <Ext url="https://antifund.com/" className="text-ink-2">
              antifund.com
            </Ext>
            ) and {m.comps} comps · public data only · no ownership, marks or private metrics
          </p>
        </div>
        <nav aria-label="Portfolio views" className="flex flex-wrap items-end gap-x-5 gap-y-2 self-end font-mono text-[10.5px] uppercase tracking-[0.08em]">
          <Link href="/portfolio/brief" className="text-ink hover:text-accent">
            Morning brief →
          </Link>
          <Link href="/portfolio/events" className="text-ink-2 hover:text-ink">
            Events
          </Link>
          <Link href="/portfolio/comps" className="text-ink-2 hover:text-ink">
            Competitive maps
          </Link>
        </nav>
      </header>

      <div className="mt-6 grid gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-demo="portfolio-counts">
        <ul aria-label="Portfolio in numbers" className="grid grid-cols-3 gap-x-8">
          {primary.map((c, i) => (
            <li key={c.key} className="min-w-0 border-t border-ink pt-2.5" title={c.title}>
              <Link href={c.href} className="group block">
                <span
                  className={clsx(
                    "tnum block font-serif text-[46px] leading-none tracking-[-0.02em] transition-colors group-hover:text-accent",
                    i === 0 && c.value > 0 ? "text-accent" : c.value > 0 ? "text-ink" : "text-ink-3",
                  )}
                >
                  {c.value}
                </span>
                <span className="label mt-2 block text-ink-2 group-hover:text-ink">
                  {c.label} <span aria-hidden className="text-ink-3">→</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
        <div className="min-w-0 border-t border-ink pt-2.5">
          <p className="label text-ink-3">By type · of the {relevant} material + watch events</p>
          <ul aria-label="Events by type" className="mt-2 grid grid-cols-3 gap-x-6 gap-y-2 sm:grid-cols-6 lg:grid-cols-3 xl:grid-cols-6">
            {byType.map((c) => (
              <li key={c.key} className="min-w-0" title={c.title}>
                <Link href={c.href} className="group block">
                  <span className={clsx("tnum block font-serif text-[26px] leading-none transition-colors group-hover:text-accent", c.value > 0 ? "text-ink" : "text-ink-3")}>
                    {c.value}
                  </span>
                  <span className="label mt-1 block text-ink-2 group-hover:text-ink">{c.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="mt-3 max-w-[110ch] text-[12px] leading-relaxed text-ink-3">
        Need attention counts companies ranked HIGH or MEDIUM. Material and to-watch count distinct events that reach a portfolio company in the window —
        its own, or a comp&apos;s through rule M8; the type columns split those {relevant} events, and a comp&apos;s event counts as competitive.
        {(watchGap > 0 || matGap > 0) && (
          <>
            {" "}
            The noise funnel (05) classifies every event about portfolio companies <em>and</em> comps ({f.material} material · {f.watch} watch):{" "}
            {watchGap > 0 && `${watchGap} watch events of non-direct comps do not reach a portfolio company`}
            {matGap > 0 && `${watchGap > 0 ? "; " : ""}${matGap} material comp events reach a company only as watch`}.
          </>
        )}
      </p>

      <div className="mt-14 space-y-16">
        <Section n={1} id="needs-attention" title="Needs attention" right={<span className="tnum">{pulse.needs_attention.length} companies</span>}>
          {pulse.needs_attention.length === 0 ? (
            <EmptyState title="No portfolio company needs attention.">
              No company has a material event or three events to watch in the window. That is a result, not a gap — the funnel below shows what was filtered.
            </EmptyState>
          ) : (
            <div className="space-y-10">
              {pulse.needs_attention.map((card, i) => (
                <AttentionCardView
                  key={card.company.id}
                  card={card}
                  rank={i + 1}
                  stage={stages.get(card.company.slug)}
                  handoff={
                    card.suggested_action === "RUN_DILIGENCE" ? (
                      <DiligenceHandoff
                        slug={card.company.slug}
                        hasWorkspace={workspaces.has(card.company.slug)}
                        writable={writable}
                        eventId={card.what_changed.find((w) => w.relation === "SELF" && /performance_claim|benchmark_claim/.test(w.subtype))?.event_id ?? null}
                      />
                    ) : undefined
                  }
                />
              ))}
            </div>
          )}
          <MethodNote section="attention">
            Priority, suggested action and questions come from documented rules; “what this could mean” is a template keyed by event types, labelled inference.
          </MethodNote>
        </Section>

        <Section
          n={2}
          id="material-events"
          title="Material events"
          right={
            <Link href="/portfolio/events?materiality=MATERIAL" className="hover:text-ink">
              {pulse.material_events.length} · all events →
            </Link>
          }
        >
          <EventList
            events={pulse.material_events}
            portfolio={portfolio}
            comps={comps}
            empty="No material event for any portfolio company in the window. Seed-stage companies often have little public activity; no change is a true result."
          />
          <p className="mt-3 text-[12px] text-ink-3">
            <Link href="/portfolio/events" className="link text-ink-2">
              {relevant} material + watch events →
            </Link>{" "}
            · <Link href="/portfolio/events?materiality=ALL" className="link text-ink-2">include the {f.noise} filtered as noise</Link>
          </p>
        </Section>

        <Section n={3} id="movers" title="Portfolio movers" right={<span className="max-sm:hidden">public proxies · comparable windows only</span>}>
          {pulse.movers.length === 0 ? (
            <p className="border-y border-rule py-4 text-[13px] text-ink-3">No comparable public-proxy change: no metric was observed on both sides of its window.</p>
          ) : (
            <ol className="border-t border-ink">
              {pulse.movers.map((mv, i) => (
                <MetricRow
                  key={`${mv.company.id}-${mv.metric.metric}-${i}`}
                  m={mv.metric}
                  company={
                    <Link href={portfolioHref(mv.company.slug)} className="hover:text-accent">
                      {mv.company.name}
                    </Link>
                  }
                />
              ))}
            </ol>
          )}
          <MethodNote section="proxies">
            Public signals, not internal metrics: open roles are a hiring signal, not headcount; HN points are attention, not adoption. Windows are dated by the
            events themselves (posting, release, story date).
          </MethodNote>
        </Section>

        <Section n={4} id="since-review" title="What changed since last review" right={<span className="tnum">{rows.length} companies</span>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse border-t border-ink text-[13px]">
              <thead>
                <tr className="border-b border-ink">
                  <th className="label px-2 py-2 text-left font-normal" scope="col">Company</th>
                  <th className="label px-2 py-2 text-left font-normal" scope="col">Last review</th>
                  <th className="label px-2 py-2 text-left font-normal" scope="col">Counting since</th>
                  <th className="label px-2 py-2 text-right font-normal" scope="col">Material</th>
                  <th className="label px-2 py-2 text-right font-normal" scope="col">Watch</th>
                  <th className="label px-2 py-2 text-right font-normal" scope="col" title="The company's own events filtered as noise">Noise</th>
                  <th className="label px-2 py-2 text-right font-normal" scope="col">Open questions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.company.id} className="border-b border-rule">
                    <td className="px-2 py-2">
                      <Link href={portfolioHref(r.company.slug)} className="font-serif text-[15.5px] text-ink hover:text-accent">
                        {r.company.name}
                      </Link>
                      <span className="ml-2 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">{r.stage}</span>
                    </td>
                    <td className="px-2 py-2 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">{r.reviewed ? "Reviewed" : "Not reviewed"}</td>
                    <td className="px-2 py-2 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
                      {r.reviewed && r.since ? fmtDate(r.since) : `window start${f.window ? ` · ${fmtDate(f.window.start, { year: false })}` : ""}`}
                    </td>
                    <td className={clsx("tnum px-2 py-2 text-right font-serif text-[18px]", r.material ? "text-accent" : "text-ink-3")}>{r.material}</td>
                    <td className={clsx("tnum px-2 py-2 text-right font-serif text-[18px]", r.watch ? "text-ink" : "text-ink-3")}>{r.watch}</td>
                    <td className="tnum px-2 py-2 text-right font-mono text-[11px] text-ink-3">{r.noise}</td>
                    <td className={clsx("tnum px-2 py-2 text-right font-mono text-[11px]", r.openQuestions ? "text-ink" : "text-ink-3")}>{r.openQuestions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[12px] leading-relaxed text-ink-3">
            {rows.every((r) => !r.reviewed)
              ? "No company has a human review on this deployment, so every row counts the whole 30-day window. "
              : "Reviewed companies count events detected after their last review; the rest count the whole 30-day window. "}
            A zero means no material or watch event was found in the public sources collected for that company — not a padded result; each portfolio view
            shows which sources were observed and which proxy windows could not be compared.
          </p>
        </Section>

        <Section n={5} id="noise" title="Noise filtered" right={f.day ? <span>run of {fmtDate(f.day)}</span> : undefined}>
          <FunnelLine funnel={f} />
          <MethodNote section="funnel">
            Every raw change in the window is counted — repository events, page edits, job postings, stories, awards — then normalised, deduplicated and
            classified by rules M1–M11. Noise is filtered by rule and shown here, not hidden.
          </MethodNote>
        </Section>

        <Section n={6} id="monitoring" title="Portfolio monitoring">
          <MonitoringBlock m={m} now={now} />
        </Section>
      </div>
    </div>
  );
}
