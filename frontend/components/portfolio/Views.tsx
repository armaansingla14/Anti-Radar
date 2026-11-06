/** Portfolio Pulse building blocks shared by /portfolio, the company portfolio view, events, comps and reports. */
import clsx from "clsx";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { domainOf, externalHref, fmtDate, fmtDateTime, pad2 } from "@/lib/format";
import { ageShort } from "@/lib/labels";
import type { AttentionCard, Comp, Funnel, MetricWindow, Monitoring, PDecision, PEvent, PortfolioAction, PQuestion } from "@/lib/types-portfolio";
import { ActionChip, CompTypeChip, InferenceTag, MaterialityBadge, PriorityBadge, QuestionStatusChip, RelationChip, RuleLink } from "./Badges";
import { QuestionActions } from "./ReviewActions";
import {
  ACTION_DEFINITION,
  actionKey,
  compsHref,
  deltaText,
  eventHref,
  funnelSteps,
  metricName,
  methodologyHref,
  portfolioHref,
  proxyDescription,
  proxyIsNot,
  proxyKind,
  relationOf,
  traceStateLabel,
  traceSteps,
  typeLabel,
  windowPhrase,
  windowText,
  type CompLink,
} from "./logic";

/** External link with ↗ (demo entities never link out). */
export function Ext({ url, children, className, demo, title }: { url: string | null | undefined; children: ReactNode; className?: string; demo?: boolean; title?: string }) {
  const href = externalHref(url, demo);
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={clsx("link", className)} title={title}>
      {children}
      <span aria-hidden className="ml-0.5 text-ink-3">↗</span>
    </a>
  );
}

/** Mono sub-heading inside a block. */
export function Sub({ children, right, className }: { children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <p className={clsx("label flex items-baseline gap-2 text-ink", className)}>
      {children}
      {right !== undefined && <span className="tnum text-ink-3">{right}</span>}
    </p>
  );
}

/** "S1 factory.com ↗ · S2 news.ycombinator.com ↗" — numbered source links, verbatim titles on hover. */
export function SourceLinks({ sources, className, demo }: { sources: { url: string | null; title: string | null }[]; className?: string; demo?: boolean }) {
  if (sources.length === 0) return <p className={clsx("text-[12px] text-ink-3", className)}>No source recorded.</p>;
  return (
    <ol className={clsx("flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10.5px]", className)}>
      {sources.map((s, i) => (
        <li key={`${s.url}-${i}`} className="min-w-0">
          <span className="text-accent">S{i + 1}</span>{" "}
          <Ext url={s.url} demo={demo} title={s.title ?? undefined} className="text-ink-2">
            {domainOf(s.url) ?? "source"}
          </Ext>
        </li>
      ))}
    </ol>
  );
}

// ── Needs Attention ──────────────────────────────────────────────────────────

export function AttentionCardView({
  card,
  rank,
  stage,
  handoff,
  compact,
}: {
  card: AttentionCard;
  rank?: number;
  stage?: string;
  /** RUN DILIGENCE handoff ("Open diligence →" / run button), rendered under the action. */
  handoff?: ReactNode;
  compact?: boolean;
}) {
  const c = card.company;
  const byId = new Map(card.what_changed.map((w) => [w.event_id, w]));
  const material = card.material_events.map((id) => ({ id, w: byId.get(id) }));
  return (
    <article
      id={`attention-${c.slug}`}
      data-demo="attention-card"
      className={clsx("grid gap-x-10 gap-y-6 border-t border-ink pt-4", !compact && "lg:grid-cols-[200px_minmax(0,1.15fr)_minmax(0,1fr)]")}
    >
      <header className="min-w-0">
        <p className="flex items-baseline gap-2">
          {rank !== undefined && <span className="tnum font-mono text-[11px] text-ink-3">{pad2(rank)}</span>}
          <Link href={portfolioHref(c.slug)} className="font-serif text-[26px] leading-[1.05] tracking-[-0.01em] text-ink decoration-accent decoration-1 underline-offset-4 hover:underline">
            {c.name}
          </Link>
          {c.is_demo && <DemoBadge />}
        </p>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <PriorityBadge value={card.priority} />
        </div>
        {stage && <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{stage}</p>}
        <div className="mt-4">
          <Sub>Suggested action</Sub>
          <div className="mt-1.5">
            <ActionChip value={card.suggested_action} />
          </div>
          <p className="mt-1.5 text-[12px] leading-snug text-ink-2">{card.action_reason}</p>
          {handoff && <div className="mt-2">{handoff}</div>}
        </div>
      </header>

      <div className="min-w-0 space-y-5">
        <div>
          <Sub>Why surfaced</Sub>
          <p className="mt-1 text-[13.5px] leading-relaxed text-ink">{card.why_surfaced}</p>
        </div>
        <div>
          <Sub right={card.what_changed.length}>What changed</Sub>
          <ul className="mt-1.5 divide-y divide-rule border-y border-rule">
            {card.what_changed.map((w) => (
              <li key={w.event_id} className="grid grid-cols-[62px_minmax(0,1fr)] items-start gap-x-3 py-2">
                <MaterialityBadge value={w.materiality} className="justify-self-start" />
                <div className="min-w-0">
                  <Link href={eventHref(w.event_id)} className="text-[13.5px] leading-snug text-ink decoration-accent underline-offset-4 hover:underline">
                    {w.title}
                  </Link>
                  <p className="mt-0.5 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
                    {fmtDate(w.event_at, { year: false })} · {typeLabel(w.event_type, w.subtype)} ·{" "}
                    {w.relation === "SELF" ? "own event" : `${w.subject} · ${w.relation.toLowerCase()}`}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        {material.length > 0 && (
          <div>
            <Sub right={material.length}>Material events</Sub>
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {material.map(({ id, w }) => (
                <li key={id}>
                  <Link
                    href={eventHref(id)}
                    className="inline-flex max-w-[34ch] items-center gap-1 truncate border border-accent px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.06em] text-accent hover:bg-accent hover:text-paper"
                    title={w?.title}
                  >
                    {w ? `${w.subject} · ${w.subtype.replace(/_/g, " ")} · ${fmtDate(w.event_at, { year: false })}` : "event"} →
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="min-w-0 space-y-5">
        <div>
          <Sub>
            What this could mean <InferenceTag />
          </Sub>
          <p className="mt-1 font-serif text-[16px] italic leading-snug text-ink-2">{card.what_this_could_mean}</p>
        </div>
        {card.questions.length > 0 && (
          <div>
            <Sub right={card.questions.length}>Questions to review</Sub>
            <ol className="mt-1.5 space-y-2">
              {card.questions.map((q, i) => (
                <li key={`${q.event_id}-${i}`} className="grid grid-cols-[20px_minmax(0,1fr)] gap-x-1 text-[13px] leading-snug text-ink">
                  <span className="tnum pt-[2px] font-mono text-[10px] text-ink-3">{pad2(i + 1)}</span>
                  <span className="min-w-0">
                    {q.text}
                    <span className="mt-0.5 block font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
                      {q.event_id && (
                        <Link href={eventHref(q.event_id)} className="hover:text-accent">
                          from event →
                        </Link>
                      )}
                      {q.source_url && (
                        <>
                          {" · "}
                          <Ext url={q.source_url} className="text-ink-3">
                            {q.source ?? domainOf(q.source_url)}
                          </Ext>
                        </>
                      )}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
        <div>
          <Sub right={card.sources.length}>Sources</Sub>
          <SourceLinks sources={card.sources} className="mt-1.5" demo={c.is_demo} />
        </div>
      </div>
    </article>
  );
}

// ── Events ───────────────────────────────────────────────────────────────────

/** "COMPETITOR · comp of Cognition (DIRECT)" — how an event reaches the portfolio. */
export function RelationLine({ e, portfolio, comps, className }: { e: PEvent; portfolio: ReadonlySet<string>; comps: Map<string, CompLink[]>; className?: string }) {
  const r = relationOf(e, portfolio, comps);
  if (r.kind === "SELF") return <RelationChip value="SELF" className={className} title="The portfolio company's own event" />;
  if (r.kind === "UNKNOWN") return <RelationChip value={e.relation ?? "—"} className={className} />;
  return (
    <span className={clsx("inline-flex flex-wrap items-baseline gap-x-1.5", className)}>
      <RelationChip value={r.kind === "COMPETITOR" ? (e.relation ?? "COMPETITOR") : "COMP"} title="A comp's event, seen from the portfolio company" />
      <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-2">
        of{" "}
        {r.via.map((v, i) => (
          <Fragment key={v.portfolio.slug}>
            {i > 0 && ", "}
            <Link href={portfolioHref(v.portfolio.slug)} className="text-ink hover:text-accent">
              {v.portfolio.name}
            </Link>{" "}
            <Link href={compsHref(v.portfolio.slug)} className="text-ink-3 hover:text-accent" title={v.why_comparable}>
              ({v.comp_type.replace(/_/g, " ").toLowerCase()})
            </Link>
          </Fragment>
        ))}
      </span>
    </span>
  );
}

/** One event in a list: date · company · type · materiality · title → event page · relation. */
export function EventRow({ e, portfolio, comps, showCompany = true }: { e: PEvent; portfolio: ReadonlySet<string>; comps: Map<string, CompLink[]>; showCompany?: boolean }) {
  return (
    <li
      data-materiality={e.materiality}
      className={clsx(
        "grid grid-cols-[58px_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-b border-rule py-2.5",
        showCompany ? "md:grid-cols-[66px_150px_minmax(0,1fr)_76px]" : "md:grid-cols-[66px_minmax(0,1fr)_76px]",
      )}
    >
      <time dateTime={e.event_at} className="tnum pt-[3px] font-mono text-[10.5px] uppercase text-ink-2" title={fmtDateTime(e.event_at)}>
        {fmtDate(e.event_at, { year: false })}
      </time>
      {showCompany && (
        <span className="min-w-0 pt-[1px] max-md:col-start-2">
          <Link href={portfolio.has(e.company.slug) ? portfolioHref(e.company.slug) : `/companies/${e.company.slug}`} className="font-serif text-[15px] leading-tight text-ink hover:text-accent">
            {e.company.name}
          </Link>
          {e.is_demo && <DemoBadge className="ml-1" />}
        </span>
      )}
      <div className="min-w-0 max-md:col-start-2">
        <Link href={eventHref(e.id)} className="text-[13.5px] leading-snug text-ink decoration-accent underline-offset-4 hover:underline">
          {e.title}
        </Link>
        <p className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
          <span>{typeLabel(e.event_type, e.event_subtype)}</span>
          {showCompany && <RelationLine e={e} portfolio={portfolio} comps={comps} />}
          <span title={e.materiality_reason}>
            {e.materiality_rule} · {e.materiality_reason}
          </span>
        </p>
      </div>
      <span className="max-md:col-start-2 md:text-right">
        <MaterialityBadge value={e.materiality} />
      </span>
    </li>
  );
}

export function EventList({ events, portfolio, comps, showCompany, empty }: { events: PEvent[]; portfolio: ReadonlySet<string>; comps: Map<string, CompLink[]>; showCompany?: boolean; empty: ReactNode }) {
  if (events.length === 0) return <p className="border-y border-rule py-4 text-[13px] leading-relaxed text-ink-3">{empty}</p>;
  return (
    <ol className="border-t border-ink">
      {events.map((e) => (
        <EventRow key={e.id} e={e} portfolio={portfolio} comps={comps} showCompany={showCompany} />
      ))}
    </ol>
  );
}

// ── Funnel ───────────────────────────────────────────────────────────────────

/** The stored run's funnel as a quiet typographic stepper. No invented numbers: unavailable says why. */
export function FunnelLine({ funnel, className }: { funnel: Funnel | null | undefined; className?: string }) {
  const steps = funnelSteps(funnel);
  if (steps.length === 0)
    return <p className={clsx("text-[13px] text-ink-3", className)}>Funnel unavailable{funnel?.reason ? `: ${funnel.reason}` : " — no portfolio run stored."}</p>;
  return (
    <ol className={clsx("flex flex-wrap items-baseline gap-x-3 gap-y-3", className)} aria-label="Noise funnel">
      {steps.map((s, i) => (
        <li key={s.key} className="flex items-baseline gap-x-3">
          {i > 0 && (
            <span aria-hidden className="font-mono text-[13px] text-ink-3">
              →
            </span>
          )}
          <span className="inline-flex flex-col">
            <span className={clsx("tnum font-serif leading-none text-ink", s.key === "classified" ? "text-[20px]" : "text-[26px]", s.key === "surfaced" && "text-accent")}>
              {s.value}
            </span>
            <span className="label mt-1 text-ink-3">{s.label}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

// ── Metrics ──────────────────────────────────────────────────────────────────

/** A comparable-window metric: "HIRING SIGNAL · new technical roles — 0 vs 1 · last 14 days vs the prior 14". */
export function MetricRow({ m, company }: { m: MetricWindow; company?: ReactNode }) {
  const not = proxyIsNot(m.proxy_label);
  return (
    <li className={clsx("grid gap-x-4 gap-y-1 border-b border-rule py-2.5", company ? "md:grid-cols-[170px_220px_minmax(0,1fr)_70px]" : "md:grid-cols-[220px_minmax(0,1fr)_70px]")}>
      {company && <span className="min-w-0 font-serif text-[15px] leading-tight text-ink">{company}</span>}
      <span className="min-w-0">
        <span className="label block text-ink">{proxyKind(m)}</span>
        <span className="mt-0.5 block text-[13px] text-ink first-letter:uppercase">{metricName(m.metric)}</span>
        {not && <span className="block font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">{not}</span>}
      </span>
      <span className={clsx("min-w-0 text-[12.5px] leading-snug", m.comparable ? "text-ink-2" : "text-ink-3")}>
        <span className={clsx(m.comparable ? "tnum font-mono text-[12px] text-ink" : "italic")}>{windowText(m)}</span>
        {m.comparable && m.proxy_label && <span className="mt-0.5 block text-[11.5px] text-ink-3">{proxyDescription(m.proxy_label)}</span>}
        {m.source_url && (
          <span className="mt-0.5 block font-mono text-[10px]">
            <Ext url={m.source_url} className="text-ink-3">
              {domainOf(m.source_url)}
            </Ext>
          </span>
        )}
      </span>
      <span className={clsx("tnum font-serif text-[20px] leading-none md:text-right", m.comparable ? "text-ink" : "text-ink-3")} title={m.comparable ? `${windowPhrase(m.window)}` : "not compared"}>
        {deltaText(m) || "—"}
      </span>
    </li>
  );
}

// ── Questions ────────────────────────────────────────────────────────────────

export function QuestionList({ questions, writable, events, empty }: { questions: PQuestion[]; writable: boolean; events?: Map<string, PEvent>; empty?: string }) {
  if (questions.length === 0)
    return <p className="border-y border-rule py-4 text-[13px] text-ink-3">{empty ?? "No open questions — none was raised by an event in the window."}</p>;
  return (
    <ol className="border-t border-ink">
      {questions.map((q, i) => {
        const ev = q.created_from_event_id ? events?.get(q.created_from_event_id) : undefined;
        return (
          <li key={q.id} className="grid grid-cols-[24px_minmax(0,1fr)] gap-x-2 border-b border-rule py-3 md:grid-cols-[24px_minmax(0,1fr)_220px]">
            <span className="tnum pt-[3px] font-mono text-[10.5px] text-ink-3">{pad2(i + 1)}</span>
            <div className="min-w-0">
              <p className="font-serif text-[16.5px] leading-snug text-ink">{q.question}</p>
              <p className="mt-1 text-[12px] leading-snug text-ink-2">
                {q.reason}
                {q.source && <span className="text-ink-3"> · {q.source}</span>}
              </p>
              {q.created_from_event_id && (
                <Link href={eventHref(q.created_from_event_id)} className="mt-1 inline-block font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2 hover:text-accent">
                  Created from {ev ? `“${ev.title.slice(0, 60)}${ev.title.length > 60 ? "…" : ""}”` : "event"} →
                </Link>
              )}
            </div>
            <div className="col-start-2 mt-2 flex flex-wrap items-center gap-2 md:col-start-3 md:mt-0 md:justify-end">
              <QuestionStatusChip value={q.status} />
              <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">Not reviewed</span>
              <QuestionActions id={q.id} status={q.status} writable={writable} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ── Decision trace ───────────────────────────────────────────────────────────

const STATE_CLS = {
  system: "border-ink text-ink",
  human: "border-accent text-accent",
  awaiting: "border-dotted border-ink-3 text-ink-3",
} as const;

/** EVENT → REVIEW → QUESTION → ACTION: system steps as logged, human steps "awaiting review" until a person acts. */
export function DecisionTrace({ decisions, suggested, max = 4, eventTitles }: { decisions: PDecision[]; suggested?: PortfolioAction | null; max?: number; eventTitles?: Map<string, string> }) {
  const steps = traceSteps(decisions, suggested);
  return (
    <ol className="grid border-t border-ink md:grid-cols-4" aria-label="Decision trace">
      {steps.map((s, i) => (
        <li key={s.key} className={clsx("min-w-0 border-b border-rule py-3 md:border-b-0 md:pr-5", i > 0 && "md:border-l md:pl-5")}>
          <p className="flex items-baseline justify-between gap-2">
            <span className="label text-ink">
              <span className="tnum mr-1.5 text-ink-3">{pad2(i + 1)}</span>
              {s.key}
            </span>
            <span className={clsx("border px-1 font-mono text-[9px] uppercase leading-[14px] tracking-[0.08em]", STATE_CLS[s.state])}>{traceStateLabel(s.state)}</span>
          </p>
          {s.lines.length === 0 ? (
            <p className="mt-2 text-[12px] leading-snug text-ink-3">{s.placeholder}</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {s.lines.slice(0, max).map((d) => (
                <li key={d.id} className="text-[12px] leading-snug text-ink-2">
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
                    {d.kind.replace(/_/g, " ")} · {d.actor}
                    {d.created_at && ` · ${fmtDate(d.created_at, { year: false })}`}
                  </span>
                  <span className="block">
                    {d.event_id && eventTitles?.get(d.event_id) && s.key === "EVENT" ? (
                      <Link href={eventHref(d.event_id)} className="hover:text-accent">
                        {d.note}
                      </Link>
                    ) : (
                      d.note
                    )}
                  </span>
                </li>
              ))}
              {s.lines.length > max && <li className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">+ {s.lines.length - max} more</li>}
            </ul>
          )}
          {s.key === "ACTION" && s.state === "awaiting" && suggested && (
            <p className="mt-2">
              <ActionChip value={suggested} />
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

// ── Comps ────────────────────────────────────────────────────────────────────

/** One comp: name → company page, type, WHY COMPARABLE (INFERENCE), source, confidence, recent events, momentum. */
export function CompRow({ comp }: { comp: Comp }) {
  const c = comp.company;
  return (
    <li className="grid gap-x-6 gap-y-2 border-b border-rule py-3 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <Link href={`/companies/${c.slug}`} className="font-serif text-[18px] leading-tight text-ink decoration-accent underline-offset-4 hover:underline">
            {c.name}
          </Link>
          <CompTypeChip value={comp.comp_type} />
          <span className="font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3" title="Stated confidence in the comparison">
            {comp.confidence} confidence
          </span>
        </p>
        <p className="mt-1 text-[13px] leading-snug text-ink">
          <span className="label mr-1.5 text-ink-3">Why comparable</span>
          {comp.why_comparable} <InferenceTag className="ml-1 align-[1px]" />
        </p>
        <p className="mt-1 font-mono text-[10px] text-ink-3">
          <Ext url={comp.source_url} className="text-ink-2">
            {domainOf(comp.source_url) ?? "source"}
          </Ext>
          {comp.technical_momentum !== null && comp.technical_momentum > 0 && (
            <span className="ml-3 uppercase tracking-[0.06em]" title="Technical momentum component of the Anti Score (public code, research, hiring)">
              Technical momentum {comp.technical_momentum.toFixed(1)}
            </span>
          )}
        </p>
      </div>
      <div className="min-w-0">
        {comp.recent_events.length === 0 ? (
          <p className="text-[12px] text-ink-3">No material or watch event in the window.</p>
        ) : (
          <ul className="space-y-1">
            {comp.recent_events.slice(0, 4).map((e) => (
              <li key={e.id} className="grid grid-cols-[48px_minmax(0,1fr)_auto] items-baseline gap-x-2">
                <span className="tnum font-mono text-[10px] uppercase text-ink-3">{fmtDate(e.event_at, { year: false })}</span>
                <Link href={eventHref(e.id)} className="min-w-0 truncate text-[12.5px] text-ink hover:text-accent" title={e.title}>
                  {e.title}
                </Link>
                <MaterialityBadge value={e.materiality} />
              </li>
            ))}
            {comp.recent_events.length > 4 && <li className="font-mono text-[9.5px] uppercase text-ink-3">+ {comp.recent_events.length - 4} more</li>}
          </ul>
        )}
      </div>
    </li>
  );
}

// ── Monitoring ───────────────────────────────────────────────────────────────

const PROVIDER_CLS: Record<string, string> = {
  healthy: "text-ink",
  degraded: "text-accent",
  stale: "text-accent",
  error: "text-accent",
  disabled: "text-ink-3",
};

export function MonitoringBlock({ m, now }: { m: Monitoring; now: Date }) {
  const stat = (label: string, v: number, title: string) => (
    <div className="border-t border-ink pt-2" title={title}>
      <dt className="label">{label}</dt>
      <dd className={clsx("tnum mt-1 font-serif text-[30px] leading-none", v ? "text-ink" : "text-ink-3")}>{v}</dd>
    </div>
  );
  return (
    <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="min-w-0">
        <dl className="grid grid-cols-3 gap-x-6 gap-y-5">
          {stat("Tracked", m.tracked, `${m.portfolio_companies} portfolio companies + ${m.comps} comps`)}
          {stat("Fresh", m.fresh, `Observed in the last ${m.stale_after_days} days`)}
          {stat("Stale", m.stale, `Not observed for more than ${m.stale_after_days} days`)}
        </dl>
        <p className="mt-3 text-[12.5px] leading-snug text-ink-2">
          {m.portfolio_companies} portfolio companies and {m.comps} comps. Fresh means observed within {m.stale_after_days} days
          {m.never_observed > 0 ? `; ${m.never_observed} never observed` : ""}.
        </p>
        <p className="mt-2 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3" suppressHydrationWarning>
          Events updated {m.events_updated_at ? `${fmtDateTime(m.events_updated_at)} (${ageShort(m.events_updated_at, now) ?? "—"})` : "—"}
          {m.data_as_of && ` · data as of ${fmtDate(m.data_as_of)}`}
        </p>
      </div>
      <div className="min-w-0 overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse border-t border-ink text-[12.5px]">
          <caption className="sr-only">Provider health for portfolio monitoring</caption>
          <thead>
            <tr className="border-b border-ink">
              <th className="label px-2 py-2 text-left font-normal" scope="col">Provider</th>
              <th className="label px-2 py-2 text-left font-normal" scope="col">State</th>
              <th className="label px-2 py-2 text-left font-normal" scope="col">Why</th>
              <th className="label whitespace-nowrap px-2 py-2 text-right font-normal" scope="col">Last success</th>
            </tr>
          </thead>
          <tbody>
            {m.providers.map((p) => (
              <tr key={p.key} className="border-b border-rule align-top">
                <td className="px-2 py-2 text-ink">{p.label}</td>
                <td className={clsx("px-2 py-2 font-mono text-[10.5px] uppercase tracking-[0.06em]", PROVIDER_CLS[p.state] ?? "text-ink-2")}>
                  {p.state === "disabled" ? "unavailable" : p.state}
                </td>
                <td className="px-2 py-2 text-[11.5px] leading-snug text-ink-2">{p.reason ?? "—"}</td>
                <td className="tnum whitespace-nowrap px-2 py-2 text-right font-mono text-[10.5px] uppercase text-ink-3" suppressHydrationWarning>
                  {p.last_success_at ? (ageShort(p.last_success_at, now) ?? fmtDate(p.last_success_at)) : "never"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** "Suggested action" block for an event with its definition. */
export function SuggestedAction({ action, children }: { action: string; children?: ReactNode }) {
  return (
    <div>
      <ActionChip value={action} />
      <p className="mt-1.5 text-[12.5px] leading-snug text-ink-2">{ACTION_DEFINITION[actionKey(action)]}</p>
      {children}
    </div>
  );
}

export function MethodNote({ children, section = "materiality", className }: { children: ReactNode; section?: Parameters<typeof methodologyHref>[0]; className?: string }) {
  return (
    <p className={clsx("mt-4 max-w-[110ch] text-[12px] leading-relaxed text-ink-3", className)}>
      {children}{" "}
      <a href={methodologyHref(section)} target="_blank" rel="noopener noreferrer" className="link text-ink-2">
        Methodology ↗
      </a>
    </p>
  );
}

export { MaterialityBadge, RuleLink };
