import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { DemoBadge } from "@/components/Badges";
import { canWrite, workspaceMap } from "@/components/diligence/logic";
import { loadDiligenceIndex, loadPortfolioComps, loadPortfolioEvent, loadPortfolioIndex } from "@/components/entity/data";
import { Section } from "@/components/entity/primitives";
import { EpistemicTag, InferenceTag, MaterialityBadge, RelationChip, RuleLink } from "@/components/portfolio/Badges";
import { DiligenceHandoff } from "@/components/portfolio/Handoff";
import { PortfolioReviewActions } from "@/components/portfolio/ReviewActions";
import { DecisionTrace, Ext, MethodNote, QuestionList, SuggestedAction } from "@/components/portfolio/Views";
import { compIndex, portfolioHref, portfolioSlugs, typeLabel } from "@/components/portfolio/logic";
import { dataSource } from "@/lib/dataSource";
import { domainOf, fmtDate, fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";

export async function generateMetadata({ params }: PageProps<"/portfolio/events/[id]">): Promise<Metadata> {
  const { id } = await params;
  const d = await loadPortfolioEvent(id).catch(() => null);
  return { title: d ? `${d.event.title} · Portfolio event` : "Portfolio event" };
}

export default async function PortfolioEventPage({ params }: PageProps<"/portfolio/events/[id]">) {
  const { id } = await params;
  const [d, session, index, maps, dIndex] = await Promise.all([loadPortfolioEvent(id), getSession(), loadPortfolioIndex(), loadPortfolioComps(), loadDiligenceIndex()]);
  if (!d) notFound();
  const e = d.event;
  const writable = canWrite(session, dataSource() === "snapshot");
  const portfolio = portfolioSlugs(index);
  const viaComps = compIndex(maps).get(e.company.slug) ?? [];
  const isPortfolio = portfolio.has(e.company.slug);
  // The portfolio company whose review this event belongs to: the subject itself, else the first impacted company.
  const reviewSlug = isPortfolio ? e.company.slug : (d.impacts[0]?.company.slug ?? null);
  const handoffSlug = d.diligence?.company_slug ?? reviewSlug;
  const workspaces = workspaceMap(dIndex);
  const hasWorkspace = handoffSlug ? workspaces.has(handoffSlug) || Boolean(d.diligence?.run_id) : false;

  return (
    <article className="pb-24">
      <header className="border-b-[3px] border-double border-ink pb-7">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/portfolio" className="label link text-ink-2">
            Portfolio Pulse
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <Link href="/portfolio/events" className="label link text-ink-2">
            Events
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label text-ink">{typeLabel(e.event_type, e.event_subtype)}</span>
          {e.is_demo && <DemoBadge />}
        </div>
        <div className="mt-5 grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0">
            <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
              <Link href={isPortfolio ? portfolioHref(e.company.slug) : `/companies/${e.company.slug}`} className="text-ink hover:text-accent">
                {e.company.name}
              </Link>
              {isPortfolio ? " · portfolio company" : viaComps.length ? ` · comp of ${viaComps.map((v) => v.portfolio.name).join(", ")}` : ""}
            </p>
            <h1 className="mt-3 max-w-[30ch] font-serif text-[40px] leading-[1.06] tracking-[-0.015em] text-ink max-md:text-[30px]">{e.title}</h1>
            {e.description && <p className="mt-4 max-w-[72ch] text-[14px] leading-relaxed text-ink-2">{e.description}</p>}
            <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2">
              <MaterialityBadge value={e.materiality} className="px-2 py-[3px] text-[10.5px]" />
              <RuleLink rule={e.materiality_rule} />
              <span className="text-[13px] text-ink">{e.materiality_reason}</span>
            </div>
            {e.novelty && <p className="mt-1.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">Novelty: {e.novelty}</p>}
          </div>
          <dl className="min-w-0 border-t border-ink text-[12.5px]">
            {[
              ["Event date", <time key="e" dateTime={e.event_at}>{fmtDate(e.event_at)}</time>],
              ["Detected", e.detected_at ? fmtDateTime(e.detected_at) : "—"],
              ["Epistemic", <EpistemicTag key="ep" value={e.epistemic} />],
              ["Confidence", `${Math.round(e.confidence * 100)}%`],
              ["Raw changes", `${e.raw_count} merged · ${e.families.join(", ") || "—"}`],
              ["Status", e.status === "REVIEWED" ? `Reviewed${e.reviewed_by ? ` by ${e.reviewed_by}` : ""}` : "Not reviewed"],
            ].map(([k, v]) => (
              <div key={k as string} className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 border-b border-rule py-1.5">
                <dt className="label text-ink-3">{k}</dt>
                <dd className="tnum text-ink">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      </header>

      <div className="mt-12 space-y-16">
        <Section n={1} id="sources" title="Sources" right={<span className="tnum">{e.sources.length}</span>}>
          <ol className="border-t border-ink">
            {e.sources.map((s, i) => (
              <li key={`${s.url}-${i}`} className="grid grid-cols-[34px_minmax(0,1fr)] gap-x-2 border-b border-rule py-2.5 md:grid-cols-[34px_minmax(0,1fr)_220px]">
                <span className="tnum pt-[2px] font-mono text-[10.5px] text-accent">S{i + 1}</span>
                <span className="min-w-0">
                  <Ext url={s.url} demo={e.is_demo} className="text-[13.5px] text-ink">
                    {s.title ?? s.url ?? "source"}
                  </Ext>
                  <span className="block truncate font-mono text-[10px] text-ink-3">{s.url}</span>
                </span>
                <span className="col-start-2 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3 md:col-start-3 md:text-right">
                  {[s.publisher?.replace(/_/g, " "), s.family, s.observed_at ? `observed ${fmtDate(s.observed_at, { year: false })}` : null].filter(Boolean).join(" · ")}
                </span>
              </li>
            ))}
          </ol>
          {e.sources.length === 0 && <p className="text-[13px] text-ink-3">No source recorded.</p>}
        </Section>

        <div className="grid gap-x-12 gap-y-16 lg:grid-cols-2">
          <Section n={2} id="why" title="Why it matters">
            <p className="font-serif text-[19px] italic leading-snug text-ink">
              {e.why_it_matters} <InferenceTag className="not-italic align-[3px]" />
            </p>
            {e.impact_areas.length > 0 && (
              <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">Impact areas: {e.impact_areas.join(" · ")}</p>
            )}
          </Section>

          <Section n={3} id="action" title="Suggested action">
            <SuggestedAction action={d.suggested_action}>
              {d.suggested_action === "RUN_DILIGENCE" && handoffSlug && (
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <DiligenceHandoff slug={handoffSlug} hasWorkspace={hasWorkspace} writable={writable} />
                  <span className="text-[12px] text-ink-3">
                    {e.performance_claim ? "The event states a performance claim that diligence can check against public evidence." : "The event bears on a diligence claim."}
                  </span>
                </div>
              )}
            </SuggestedAction>
            {reviewSlug && <PortfolioReviewActions slug={reviewSlug} eventId={e.id} writable={writable} label="+ Record event review" />}
          </Section>
        </div>

        <Section n={4} id="impacts" title="Impacts" right={<span className="tnum">{d.impacts.length} portfolio {d.impacts.length === 1 ? "company" : "companies"}</span>}>
          {d.impacts.length === 0 ? (
            <p className="border-y border-rule py-4 text-[13px] leading-relaxed text-ink-3">
              This event reaches no portfolio company.{" "}
              {viaComps.length
                ? `${e.company.name} is ${viaComps.map((v) => `a ${v.comp_type.replace(/_/g, " ").toLowerCase()} comp of ${v.portfolio.name}`).join(" and ")}; under rule M8 only a DIRECT comp's watch event, or a comp's material event, propagates.`
                : "Noise never propagates."}
            </p>
          ) : (
            <ul className="border-t border-ink">
              {d.impacts.map((im) => (
                <li key={im.company.id} className="grid gap-x-6 gap-y-1.5 border-b border-rule py-3 md:grid-cols-[200px_minmax(0,1fr)_150px]">
                  <span className="min-w-0">
                    <Link href={portfolioHref(im.company.slug)} className="font-serif text-[18px] text-ink hover:text-accent">
                      {im.company.name}
                    </Link>
                    <span className="mt-1 flex flex-wrap gap-1.5">
                      <RelationChip value={im.relation} />
                    </span>
                  </span>
                  <p className="text-[13px] leading-snug text-ink">
                    <span className="label mr-1.5 text-ink-3">Impact reason</span>
                    {im.impact_reason}
                    {im.relation !== "SELF" && <InferenceTag className="ml-1.5 align-[1px]" />}
                  </p>
                  <span className="flex flex-wrap items-baseline gap-2 md:flex-col md:items-end">
                    <MaterialityBadge value={im.materiality_for_company} />
                    <span className="tnum font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">for the company · confidence {Math.round(im.confidence * 100)}%</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <MethodNote section="comps">Impact confidence is the event&apos;s confidence scaled by the comp&apos;s stated confidence (HIGH 1.0 · MEDIUM 0.8 · LOW 0.6).</MethodNote>
        </Section>

        <Section n={5} id="questions" title="Questions raised" right={<span className="tnum">{d.questions.length}</span>}>
          <QuestionList
            questions={d.questions}
            writable={writable}
            empty="This event raised no question."
          />
        </Section>

        <Section n={6} id="decision-trace" title="Decision trace" right={<span className="max-sm:hidden">event → review → question → action</span>}>
          <DecisionTrace decisions={d.decision_trace} suggested={d.suggested_action} max={6} />
        </Section>

        <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
          Event {e.id.slice(0, 8)} · {e.source_urls.length} source URL{e.source_urls.length === 1 ? "" : "s"}
          {e.source_urls[0] && ` · primary ${domainOf(e.source_urls[0])}`}
        </p>
      </div>
    </article>
  );
}
