import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { DataAge } from "@/components/DataAge";
import { DiligenceStateBadge } from "@/components/diligence/Badges";
import { canWrite, countsLine, tabHref } from "@/components/diligence/logic";
import { loadPortfolioComps, loadPortfolioCompany, loadPortfolioIndex } from "@/components/entity/data";
import { Contents, Section } from "@/components/entity/primitives";
import { ActionChip, CompTypeChip, InferenceTag, PriorityBadge } from "@/components/portfolio/Badges";
import { DiligenceHandoff } from "@/components/portfolio/Handoff";
import { PortfolioReviewActions } from "@/components/portfolio/ReviewActions";
import { DecisionTrace, EventList, Ext, MethodNote, MetricRow, QuestionList, Sub } from "@/components/portfolio/Views";
import { compIndex, portfolioSlugs, positionFacts, proxyDescription, proxyIsNot, proxyKind, metricName, reviewText, standaloneProxies, stripPrivate } from "@/components/portfolio/logic";
import { dataSource, displayNow } from "@/lib/dataSource";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import type { Comp, PEvent } from "@/lib/types-portfolio";

export async function generateMetadata({ params }: PageProps<"/portfolio/[company]">): Promise<Metadata> {
  const { company } = await params;
  const v = await loadPortfolioCompany(decodeURIComponent(company)).catch(() => null);
  return { title: v ? `Portfolio · ${v.company.name}` : "Portfolio" };
}

function Fact({ k, children, title }: { k: string; children: ReactNode; title?: string }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 border-b border-rule py-1.5" title={title}>
      <dt className="label text-ink-3">{k}</dt>
      <dd className="text-[12.5px] leading-snug text-ink">{children}</dd>
    </div>
  );
}

const fmtVal = (v: number | null) => (v === null ? "unknown" : Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(1));

export default async function PortfolioCompanyPage({ params }: PageProps<"/portfolio/[company]">) {
  const { company } = await params;
  const [raw, session, index, maps] = await Promise.all([loadPortfolioCompany(decodeURIComponent(company)), getSession(), loadPortfolioIndex(), loadPortfolioComps()]);
  if (!raw) notFound();
  // Defence in depth: the API serialises positions from an allow-list; nothing private renders even if one slipped in.
  const v = stripPrivate(raw);
  const c = v.company;
  const writable = canWrite(session, dataSource() === "snapshot");
  const now = displayNow();
  const facts = positionFacts(v.position);
  const portfolio = portfolioSlugs(index);
  portfolio.add(c.slug);
  const comps = compIndex(maps);
  const card = v.needs_attention;
  const own = v.events.filter((e) => e.materiality !== "NOISE");
  const compByslug = new Map<string, Comp>(v.comps.map((x) => [x.company.slug, x]));
  const competitor = new Map<string, PEvent[]>();
  for (const e of v.competitor_events) competitor.set(e.company.slug, [...(competitor.get(e.company.slug) ?? []), e]);
  const titles = new Map([...v.events, ...v.competitor_events].map((e) => [e.id, e.title]));
  const events = new Map([...v.events, ...v.competitor_events].map((e) => [e.id, e]));
  const hasWorkspace = v.diligence.status !== "NOT_RUN" && Boolean(v.diligence.run_id);
  const proxies = standaloneProxies(v.proxies);
  const suggested = card?.suggested_action ?? (own.some((e) => e.materiality === "MATERIAL") ? "REVIEW" : own.length ? "MONITOR" : "NO_ACTION");

  const sections = [
    ["material-events", "Material events"],
    ["competitive-changes", "Competitive changes"],
    ["signals", "Current signals · public proxies"],
    ["questions", "Open questions"],
    ["review", "Last review · next review"],
    ["decision-trace", "Decision trace"],
    ["freshness", "Data freshness"],
  ] as const;
  const n = (id: (typeof sections)[number][0]) => sections.findIndex((s) => s[0] === id) + 1;

  return (
    <article className="pb-24">
      <header className="border-b-[3px] border-double border-ink pb-7" data-demo="portfolio-header">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/portfolio" className="label link text-ink-2">
            Portfolio Pulse
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label text-ink">Portfolio view</span>
          {c.is_demo && <DemoBadge />}
        </div>
        <div className="mt-5 grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="min-w-0">
            <h1 className="font-serif text-[52px] leading-[1.0] tracking-[-0.02em] text-ink max-md:text-[38px]">
              <Link href={`/companies/${c.slug}`} className="decoration-accent decoration-1 underline-offset-[6px] hover:underline">
                {c.name}
              </Link>
            </h1>
            {v.position.sector && <p className="mt-3 max-w-[60ch] font-serif text-[19px] italic leading-snug text-ink-2">{v.position.sector}</p>}
            <nav aria-label="Company views" className="mt-4 flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10.5px] uppercase tracking-[0.08em]">
              <Link href={tabHref(c.slug, "overview")} className="text-ink-2 hover:text-ink">
                Company profile →
              </Link>
              <Link href={tabHref(c.slug, "radar")} className="text-ink-2 hover:text-ink">
                Radar
              </Link>
              <Link href={tabHref(c.slug, "diligence")} className="text-ink-2 hover:text-ink">
                Diligence
              </Link>
              <Link href={tabHref(c.slug, "comps")} className="text-ink-2 hover:text-ink">
                Comps
              </Link>
              <Link href={tabHref(c.slug, "reports")} className="text-ink-2 hover:text-ink">
                Reports
              </Link>
              {v.report_id && (
                <Link href={`/reports/${v.report_id}`} className="text-ink hover:text-accent">
                  Review brief →
                </Link>
              )}
            </nav>

            <div className="mt-7 grid gap-x-8 gap-y-5 border-t border-ink pt-4 md:grid-cols-2">
              <div>
                <Sub>Status</Sub>
                <p className="mt-1.5 flex flex-wrap items-center gap-2">
                  {card ? <PriorityBadge value={card.priority} /> : <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">Not in Needs Attention</span>}
                </p>
                <p className="mt-1.5 text-[13px] leading-snug text-ink">
                  {card?.why_surfaced ??
                    `${own.length} own event${own.length === 1 ? "" : "s"} and ${v.competitor_events.length} competitor event${v.competitor_events.length === 1 ? "" : "s"} to read in the window; ${v.noise_count} filtered as noise.`}
                </p>
              </div>
              <div>
                <Sub>Suggested action</Sub>
                <p className="mt-1.5">
                  <ActionChip value={suggested} />
                </p>
                <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">{card?.action_reason ?? "Derived from the events below — no human review yet."}</p>
              </div>
              {card && (
                <div className="md:col-span-2">
                  <Sub>
                    What this could mean <InferenceTag />
                  </Sub>
                  <p className="mt-1 font-serif text-[17px] italic leading-snug text-ink-2">{card.what_this_could_mean}</p>
                </div>
              )}
            </div>
          </div>

          <aside className="min-w-0 space-y-6">
            <div>
              <Sub>Public investment context</Sub>
              <dl className="mt-2 border-t border-ink">
                <Fact k="Status">Publicly disclosed Anti Fund portfolio company</Fact>
                <Fact k="Public source">
                  <Ext url={v.position.source_url}>antifund.com</Ext>
                  {v.position.source_checked_at && <span className="text-ink-3"> · checked {fmtDate(v.position.source_checked_at)}</span>}
                </Fact>
                <Fact k="Stage" title="Exactly as the public source states it">
                  {facts.stage}
                </Fact>
                <Fact k="Year">{facts.year}</Fact>
                <Fact k="Round" title="No public source states the round">
                  <span className="text-ink-3">{facts.round}</span>
                </Fact>
              </dl>
              <p className="mt-2 text-[11.5px] leading-snug text-ink-3">Ownership, cost basis, marks and other private fields are not shown in public mode.</p>
            </div>
            <div>
              <Sub>Diligence</Sub>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-ink pt-2">
                <DiligenceStateBadge state={v.diligence.status} />
                {v.diligence.counts && Object.keys(v.diligence.counts).length > 0 && (
                  <span className="tnum font-mono text-[10.5px] text-ink-2">{countsLine(v.diligence.counts)}</span>
                )}
                <DiligenceHandoff slug={c.slug} hasWorkspace={hasWorkspace} writable={writable} />
              </div>
            </div>
          </aside>
        </div>
      </header>

      <Contents items={sections.map(([id, title]) => ({ id, title }))} />

      <div className="mt-12 space-y-16">
        <Section n={n("material-events")} id="material-events" title="Material events" right={<span className="tnum">{own.length} own · {v.noise_count} noise filtered</span>}>
          <EventList
            events={own}
            portfolio={portfolio}
            comps={comps}
            showCompany={false}
            empty={`No material or watch event of ${c.name}'s own in the 30-day window — a true result for the public sources collected, not padded.`}
          />
          <MethodNote>Materiality comes from documented rules M1–M11; each event page shows its rule and reason.</MethodNote>
        </Section>

        <Section n={n("competitive-changes")} id="competitive-changes" title="Competitive changes" right={<span className="tnum">{v.competitor_events.length} events · {v.comps.length} comps</span>}>
          {competitor.size === 0 ? (
            <p className="border-y border-rule py-4 text-[13px] text-ink-3">
              No comp event reached {c.name} in the window. Only a DIRECT comp&apos;s watch event, or any comp&apos;s material event, propagates (rule M8).
            </p>
          ) : (
            <div className="space-y-8">
              {[...competitor.entries()].map(([slug, evs]) => {
                const comp = compByslug.get(slug);
                return (
                  <div key={slug}>
                    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <Link href={`/companies/${slug}`} className="font-serif text-[19px] text-ink hover:text-accent">
                        {evs[0].company.name}
                      </Link>
                      {comp && <CompTypeChip value={comp.comp_type} />}
                      {comp && <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">{comp.confidence} confidence</span>}
                    </p>
                    {comp && (
                      <p className="mt-1 text-[12.5px] leading-snug text-ink-2">
                        <span className="label mr-1.5 text-ink-3">Impact reason</span>
                        {evs[0].company.name} is a {comp.comp_type.replace(/_/g, " ").toLowerCase()} comp of {c.name}: {comp.why_comparable}{" "}
                        <InferenceTag className="ml-1 align-[1px]" />
                      </p>
                    )}
                    <div className="mt-2">
                      <EventList events={evs} portfolio={portfolio} comps={comps} showCompany={false} empty="" />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Section>

        <Section n={n("signals")} id="signals" title="Current signals · public proxies" right={<span className="max-sm:hidden">comparable windows</span>}>
          {v.metrics.length > 0 && (
            <ol className="border-t border-ink">
              {v.metrics.map((m) => (
                <MetricRow key={m.metric} m={m} />
              ))}
            </ol>
          )}
          {proxies.length > 0 && (
            <div className="mt-6">
              <Sub>Latest public proxies · {fmtDate(proxies[0].day)}</Sub>
              <ul className="mt-2 grid gap-x-8 border-t border-ink sm:grid-cols-2">
                {proxies.map((p) => {
                  const not = proxyIsNot(p.proxy_label);
                  return (
                    <li key={p.metric} className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 border-b border-rule py-2.5">
                      <span className="tnum font-serif text-[24px] leading-none text-ink">{fmtVal(p.value)}</span>
                      <span className="min-w-0">
                        <span className="label block text-ink">{proxyKind(p)}</span>
                        <span className="block text-[12.5px] leading-snug text-ink first-letter:uppercase">{metricName(p.metric)}</span>
                        <span className="block text-[11.5px] leading-snug text-ink-3">
                          {proxyDescription(p.proxy_label)}
                          {not && ` · ${not}`}
                        </span>
                        {p.source_url && (
                          <Ext url={p.source_url} className="font-mono text-[10px] text-ink-3">
                            source
                          </Ext>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <MethodNote section="proxies">Public proxies, never internal metrics. A window pair observed on only one side says “not compared” and why.</MethodNote>
        </Section>

        <Section n={n("questions")} id="questions" title="Open questions" right={<span className="tnum">{v.questions.length}</span>}>
          <QuestionList questions={v.questions} writable={writable} events={events} />
        </Section>

        <Section n={n("review")} id="review" title="Last review · next review">
          <dl className="grid gap-x-10 gap-y-4 border-t border-ink pt-3 sm:grid-cols-3">
            <div>
              <dt className="label text-ink-3">Last review</dt>
              <dd className="mt-1 text-[14px] text-ink">
                {reviewText(v.last_review)}
                {v.last_review?.created_at && <span className="block font-mono text-[10.5px] text-ink-3">{fmtDateTime(v.last_review.created_at)}</span>}
                {v.last_review?.note && <span className="block text-[12.5px] text-ink-2">{v.last_review.note}</span>}
              </dd>
            </div>
            <div>
              <dt className="label text-ink-3">Next review</dt>
              <dd className="mt-1 text-[14px] text-ink">{v.next_review ? fmtDate(v.next_review) : <span className="text-ink-3">Not scheduled — set 30 days after a review</span>}</dd>
            </div>
            <div>
              <dt className="label text-ink-3">Review memo</dt>
              <dd className="mt-1 text-[14px] text-ink">
                {v.report_id ? (
                  <Link href={`/reports/${v.report_id}`} className="link">
                    Portfolio review brief →
                  </Link>
                ) : (
                  <span className="text-ink-3">No stored review memo</span>
                )}
              </dd>
            </div>
          </dl>
          <PortfolioReviewActions slug={c.slug} writable={writable} />
        </Section>

        <Section n={n("decision-trace")} id="decision-trace" title="Decision trace" right={<span className="max-sm:hidden">event → review → question → action</span>}>
          <DecisionTrace decisions={v.decision_trace} suggested={suggested} eventTitles={titles} />
          <MethodNote section="review">The system logs only its own steps. Human steps stay “awaiting review” until a person records one under their own name.</MethodNote>
        </Section>

        <Section n={n("freshness")} id="freshness" title="Data freshness">
          <div className="border-y border-rule py-2.5">
            <DataAge age={v.data_age} now={now} />
          </div>
        </Section>
      </div>
    </article>
  );
}
