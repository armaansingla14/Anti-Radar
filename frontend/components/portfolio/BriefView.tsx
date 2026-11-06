/** PORTFOLIO MORNING BRIEF — rendered from the stored `portfolio_brief` report (page and report reader). */
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { longDate } from "@/components/feed/prose";
import { pad2 } from "@/lib/format";
import type { BriefSource, Funnel, PortfolioBriefBody, ReportClaim, ReportQuestion } from "@/lib/types-portfolio";
import { ActionChip, EpistemicTag, InferenceTag, MaterialityBadge, PriorityBadge } from "./Badges";
import { Ext, FunnelLine } from "./Views";
import { eventHref, portfolioHref, stripCites, thingsWorthKnowing } from "./logic";

export function Cites({ ids, prefix = "psrc" }: { ids: string[] | undefined; prefix?: string }) {
  if (!ids?.length) return null;
  const shown = ids.slice(0, 4);
  return (
    <>
      {shown.map((id) => (
        <sup key={id} className="ml-[2px] font-mono text-[0.68em] leading-none">
          <a href={`#${prefix}-${id}`} className="text-accent no-underline hover:underline" title={`Source ${id}`}>
            {id}
          </a>
        </sup>
      ))}
      {ids.length > shown.length && <sup className="ml-[2px] font-mono text-[0.68em] text-ink-3">+{ids.length - shown.length}</sup>}
    </>
  );
}

export function BriefHeading({ n, children, right }: { n?: number; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-ink pb-1.5">
      <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink">
        {n !== undefined && <span className="tnum mr-2 text-ink-3">{pad2(n)}</span>}
        {children}
      </h2>
      {right && <span className="label text-ink-3">{right}</span>}
    </div>
  );
}

function ClaimItem({ c, index, prefix }: { c: ReportClaim; index?: number; prefix?: string }) {
  return (
    <li className={clsx("grid gap-x-3 py-2.5", index !== undefined ? "grid-cols-[26px_minmax(0,1fr)]" : "grid-cols-[minmax(0,1fr)]")}>
      {index !== undefined && <span className="tnum pt-[3px] font-mono text-[11px] text-ink-3">{pad2(index + 1)}</span>}
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {c.company && c.company_slug ? (
            <Link href={portfolioHref(c.company_slug)} className="font-serif text-[17px] leading-tight text-ink hover:text-accent">
              {c.company}
            </Link>
          ) : c.company ? (
            <span className="font-serif text-[17px] leading-tight text-ink">{c.company}</span>
          ) : null}
          {c.materiality && <MaterialityBadge value={c.materiality} />}
          {c.relation && c.relation !== "SELF" && <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">via a {c.relation.toLowerCase()}</span>}
        </p>
        <p className="mt-0.5 text-[13.5px] leading-snug text-ink">
          {c.event_id ? (
            <Link href={eventHref(c.event_id)} className="decoration-accent underline-offset-4 hover:underline">
              {stripCites(c.text)}
            </Link>
          ) : (
            stripCites(c.text)
          )}
          <Cites ids={c.source_ids} prefix={prefix} />
          {c.epistemic === "INFERENCE" && <InferenceTag className="ml-2 align-[1px]" />}
        </p>
      </div>
    </li>
  );
}

export function ClaimList({ claims, numbered, empty, prefix }: { claims: ReportClaim[]; numbered?: boolean; empty: string; prefix?: string }) {
  if (claims.length === 0) return <p className="text-[13px] text-ink-3">{empty}</p>;
  return (
    <ol className="divide-y divide-rule">
      {claims.map((c, i) => (
        <ClaimItem key={`${c.event_id}-${i}`} c={c} index={numbered ? i : undefined} prefix={prefix} />
      ))}
    </ol>
  );
}

function Questions({ qs, prefix }: { qs: ReportQuestion[]; prefix?: string }) {
  if (qs.length === 0) return null;
  return (
    <ol className="mt-2 space-y-1">
      {qs.map((q, i) => (
        <li key={`${q.event_id}-${i}`} className="grid grid-cols-[20px_minmax(0,1fr)] text-[13px] leading-snug text-ink">
          <span className="tnum font-mono text-[10px] text-ink-3">{pad2(i + 1)}</span>
          <span>
            {q.question}
            <Cites ids={q.source_ids} prefix={prefix} />
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Funnel from a stored `noise_filtered` block (as stored — never recomputed). */
export function storedFunnel(nf: PortfolioBriefBody["noise_filtered"] | null | undefined): Funnel | null {
  if (!nf || typeof nf.raw_changes !== "number") return null;
  return {
    raw_changes: nf.raw_changes ?? 0,
    candidates: nf.candidates ?? 0,
    after_dedup: nf.after_dedup ?? 0,
    material: nf.material ?? 0,
    watch: nf.watch ?? 0,
    noise: nf.noise ?? 0,
    companies_surfaced: nf.companies_surfaced ?? 0,
    day: null,
    window: null,
    available: true,
    reason: null,
  };
}

export function SourceIndex({ sources, prefix = "psrc" }: { sources: BriefSource[]; prefix?: string }) {
  return (
    <ol className="grid gap-x-8 gap-y-1 md:grid-cols-2">
      {sources.map((s) => (
        <li key={s.id} id={`${prefix}-${s.id}`} className="grid scroll-mt-24 grid-cols-[34px_minmax(0,1fr)] gap-x-1 text-[12px] leading-snug">
          <span className="tnum font-mono text-[10.5px] text-accent">{s.id}</span>
          <Ext url={s.url} className="min-w-0 truncate text-ink-2" title={s.title ?? undefined}>
            {s.title ?? s.url}
          </Ext>
        </li>
      ))}
    </ol>
  );
}

/** The brief body: N THINGS WORTH KNOWING, NEEDS ATTENTION, TECHNICAL / COMPETITIVE / TALENT, NOISE FILTERED. */
export function PortfolioBriefSections({ body, prefix = "psrc", numbered = true }: { body: PortfolioBriefBody; prefix?: string; numbered?: boolean }) {
  const things = body.five_things ?? [];
  const funnel = storedFunnel(body.noise_filtered);
  let n = 0;
  const next = () => (numbered ? ++n : undefined);
  return (
    <div className="space-y-12">
      <section id="r-sec-five_things" className="scroll-mt-24">
        <BriefHeading n={next()} right={`${things.length} items`}>
          {thingsWorthKnowing(things.length)}
        </BriefHeading>
        <ClaimList claims={things} numbered empty="Nothing material or worth watching was published in the window — a true result, not padded." prefix={prefix} />
      </section>

      <section id="r-sec-needs_attention" className="scroll-mt-24">
        <BriefHeading n={next()} right={`${body.needs_attention.length} companies`}>
          Needs attention
        </BriefHeading>
        {body.needs_attention.length === 0 ? (
          <p className="text-[13px] text-ink-3">No portfolio company ranked HIGH or MEDIUM priority.</p>
        ) : (
          <ol className="space-y-6">
            {body.needs_attention.map((a, i) => (
              <li key={a.company.slug} className="grid grid-cols-[26px_minmax(0,1fr)] gap-x-3 border-t border-rule pt-3 first:border-t-0 first:pt-0">
                <span className="tnum pt-[5px] font-mono text-[11px] text-ink-3">{pad2(i + 1)}</span>
                <div className="min-w-0">
                  <p className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <Link href={portfolioHref(a.company.slug)} className="font-serif text-[20px] leading-tight text-ink hover:text-accent">
                      {a.company.name}
                    </Link>
                    <PriorityBadge value={a.priority} />
                    <ActionChip value={a.suggested_action} />
                  </p>
                  <p className="mt-1 text-[13px] leading-snug text-ink-2">
                    {stripCites(a.why_surfaced.text)}
                    <Cites ids={a.why_surfaced.source_ids} prefix={prefix} />
                  </p>
                  <p className="mt-1 font-serif text-[15.5px] italic leading-snug text-ink-2">
                    {stripCites(a.what_this_could_mean.text)} <InferenceTag className="not-italic align-[2px]" />
                  </p>
                  <p className="mt-1 text-[12.5px] text-ink">
                    <span className="label mr-1.5 text-ink-3">Why this action</span>
                    {a.action_reason}
                  </p>
                  <Questions qs={a.questions} prefix={prefix} />
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section id="r-sec-technical_changes" className="scroll-mt-24">
        <BriefHeading n={next()} right={body.technical_changes.length}>
          Technical changes
        </BriefHeading>
        <ClaimList claims={body.technical_changes} empty="No technical event to watch in the window." prefix={prefix} />
      </section>

      <section id="r-sec-competitive_moves" className="scroll-mt-24">
        <BriefHeading n={next()} right={body.competitive_moves.length}>
          Competitive moves
        </BriefHeading>
        <ClaimList claims={body.competitive_moves} empty="No comp event reached a portfolio company in the window." prefix={prefix} />
      </section>

      <section id="r-sec-talent_changes" className="scroll-mt-24">
        <BriefHeading n={next()} right={body.talent_changes.length}>
          Talent changes
        </BriefHeading>
        <ClaimList claims={body.talent_changes} empty="No talent event in the window. Hiring signals are public postings, never headcount." prefix={prefix} />
      </section>

      <section id="r-sec-noise_filtered" className="scroll-mt-24">
        <BriefHeading n={next()}>Noise filtered</BriefHeading>
        {funnel ? <FunnelLine funnel={funnel} /> : <p className="text-[13px] text-ink-3">{body.noise_filtered?.text ?? "Funnel not stored with this brief."}</p>}
      </section>

      {body.limitations && (
        <p className="border-t border-rule pt-3 text-[12px] leading-relaxed text-ink-3">
          <span className="label mr-2 text-ink-2">Limitations</span>
          {body.limitations}
        </p>
      )}
    </div>
  );
}

/** Masthead + sections + source index: the printable PORTFOLIO MORNING BRIEF. */
export function PortfolioBriefView({ body, sources, actions, generatedNote }: { body: PortfolioBriefBody; sources: BriefSource[]; actions?: ReactNode; generatedNote?: ReactNode }) {
  return (
    <div className="brief-print mx-auto max-w-[860px]">
      {actions && <div className="mb-8 flex flex-wrap items-center justify-between gap-4 print:hidden">{actions}</div>}
      <header className="border-t-[3px] border-ink pt-1">
        <div className="border-t border-ink pt-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
            <p className="font-mono text-[11.5px] uppercase tracking-[0.2em] text-ink">
              Anti <span className="text-accent">Radar</span> · Portfolio Pulse · {longDate(body.date, { year: true })}
            </p>
            <p className="label text-ink-3">Portfolio morning brief</p>
          </div>
          <h1 className="mt-4 font-serif text-[44px] leading-[1.04] tracking-[-0.018em] text-ink max-md:text-[34px]">
            {thingsWorthKnowing((body.five_things ?? []).length)}
          </h1>
          <p className="mt-3 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2">
            Window {body.period.start} → {body.period.end} ({body.period.days} days) · {body.agent} · public sources only
            <EpistemicTag value="FACT" className="ml-3 align-[1px]" />
            <span className="ml-1 normal-case tracking-normal text-ink-3">observed in a cited source</span>
            <InferenceTag className="ml-2 align-[1px]" />
            <span className="ml-1 normal-case tracking-normal text-ink-3">derived</span>
          </p>
          {generatedNote && <p className="mt-2 text-[12px] text-ink-3">{generatedNote}</p>}
        </div>
      </header>
      <div className="mt-10">
        <PortfolioBriefSections body={body} />
      </div>
      <section className="mt-12 border-t-[3px] border-double border-ink pt-3" aria-labelledby="psrc-h">
        <h2 id="psrc-h" className="mb-3 flex items-baseline justify-between">
          <span className="font-serif text-[22px] text-ink">Sources</span>
          <span className="label tnum">{sources.length} cited</span>
        </h2>
        <SourceIndex sources={sources} />
      </section>
    </div>
  );
}
