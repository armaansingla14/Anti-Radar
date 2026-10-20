/**
 * Home sections: count strip, WHAT CHANGED, and compact rows for formations,
 * people, companies and strange signals. Server components; the only client
 * islands are the source drawers.
 */
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { CollisionLine } from "@/components/CollisionStrip";
import { Confidence } from "@/components/Confidence";
import { Delta } from "@/components/Delta";
import { SourcesButton } from "@/components/SourceDrawer";
import { FormationLabel } from "@/components/formation/Labels";
import { formationHref, MemberLine } from "@/components/formation/FormationCardView";
import { relativeTime } from "@/components/insight/relativeTime";
import { fmtCompact, fmtDate, fmtDateTime, hrefFor, pad2 } from "@/lib/format";
import { familyLabels, isCollision } from "@/lib/labels";
import type { ChangeItem, FormationCard, HomeSummary, IntelCard, MeetCard, StrangeSignal } from "@/lib/types";
import { changeHref, changeIndex, changeKindLabel, COUNT_DEFS } from "./homeLogic";
import { EntityLinks } from "./EntityLinks";
import { patternLabel } from "./StrangeSignalDispatch";

const more = "label shrink-0 text-ink transition-colors hover:text-accent";

/** Section head: mono caps label, hairline, count, "All →" link. */
export function HomeSection({
  id,
  title,
  count,
  href,
  hrefLabel,
  children,
  className,
}: {
  id: string;
  title: string;
  count?: ReactNode;
  href?: string;
  hrefLabel?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby={`${id}-h`} id={id} className={clsx("min-w-0 scroll-mt-6", className)}>
      <div className="flex items-center gap-3 border-b border-ink pb-2">
        <h2 id={`${id}-h`} className="label shrink-0 text-ink">
          {title}
        </h2>
        {count !== undefined && <span className="label tnum shrink-0 text-ink-3">{count}</span>}
        <span aria-hidden className="flex-1" />
        {href && (
          <Link href={href} className={more}>
            {hrefLabel ?? "All"} →
          </Link>
        )}
      </div>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="py-4 text-[13px] leading-snug text-ink-3">{children}</p>;
}

// ── Counts ───────────────────────────────────────────────────────────────────

export function HomeCounts({ summary }: { summary: HomeSummary }) {
  return (
    <ul aria-label="Today in numbers" className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-3 lg:grid-cols-5">
      {COUNT_DEFS.map((d, i) => {
        const n = summary[d.key];
        return (
          <li key={d.key} className="min-w-0 border-t border-ink pt-2.5">
            <Link href={d.href} className="group block">
              <span
                className={clsx(
                  "tnum block font-serif text-[46px] leading-none tracking-[-0.02em] transition-colors group-hover:text-accent",
                  i === 0 && n > 0 ? "text-accent" : n > 0 ? "text-ink" : "text-ink-3",
                )}
              >
                {fmtCompact(n)}
              </span>
              <span className="label mt-2 block text-ink-2 transition-colors group-hover:text-ink">
                {d.label}{" "}
                <span aria-hidden className="text-ink-3">
                  →
                </span>
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

// ── What changed ─────────────────────────────────────────────────────────────

function When({ at, now }: { at: string | null; now: Date }) {
  if (!at) return <span className="text-ink-3">undated</span>;
  return (
    <time dateTime={at} title={fmtDateTime(at)}>
      {relativeTime(at, now) ?? fmtDate(at)}
    </time>
  );
}

export function WhatChanged({ items, now }: { items: ChangeItem[]; now: Date }) {
  if (items.length === 0) return <Empty>Nothing changed in this window. Widen the filter or check provider status for ingestion gaps.</Empty>;
  return (
    <ol className="divide-y divide-rule">
      {items.map((it, i) => {
        const href = changeHref(it);
        return (
          <li
            key={`${it.kind}-${it.entity?.id ?? it.headline}-${i}`}
            data-card
            data-href={href ?? undefined}
            tabIndex={0}
            className="grid grid-cols-[30px_minmax(0,1fr)] gap-x-3 py-3 outline-none focus-visible:bg-accent-wash"
          >
            <span className="tnum pt-[1px] font-mono text-[12px] text-ink">{changeIndex(i)}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-2">
                <span className="text-ink">{changeKindLabel(it.kind)}</span>
                <span aria-hidden className="text-ink-3">
                  ·
                </span>
                <When at={it.at} now={now} />
                {it.label && it.label.toUpperCase() !== changeKindLabel(it.kind).toUpperCase() && (
                  <>
                    <span aria-hidden className="text-ink-3">
                      ·
                    </span>
                    <span className="text-ink-3">{it.label}</span>
                  </>
                )}
                {it.is_demo && <DemoBadge />}
                <span className="ml-auto flex items-center gap-x-4">
                  <Confidence value={it.confidence} />
                  <SourcesButton claim={it.headline} evidence={it.evidence} demo={Boolean(it.is_demo)} className="text-[10px]" />
                  {href && (
                    <Link href={href} data-action="open" className="text-ink-2 hover:text-ink">
                      Open →
                    </Link>
                  )}
                </span>
              </div>
              <h3 className="mt-1 font-serif text-[18px] leading-[1.25] tracking-[-0.005em] text-ink">
                {href ? (
                  <Link href={href} className="decoration-accent decoration-1 underline-offset-4 hover:underline" tabIndex={-1}>
                    {it.headline}
                  </Link>
                ) : (
                  it.headline
                )}
              </h3>
              {it.why_now && (
                <p className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-ink-2">
                  <span className="mr-2 font-mono text-[10px] uppercase tracking-[0.08em] text-ink">Why now</span>
                  {it.why_now}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ── Formations ───────────────────────────────────────────────────────────────

export function FormationRows({ cards, stacked = false }: { cards: FormationCard[]; stacked?: boolean }) {
  if (cards.length === 0)
    return <Empty>No formation candidates in this window. Formation needs dated evidence of a team or a young organization converging.</Empty>;
  return (
    <ol className={clsx("grid gap-x-8", stacked ? "divide-y divide-rule" : "md:grid-cols-3")}>
      {cards.map((c, i) => {
        const href = formationHref(c);
        const fams = familyLabels(c.families);
        return (
          <li key={c.id} className={clsx("min-w-0 py-3", !stacked && "border-b border-rule md:border-b-0")}>
            <div className="flex items-center justify-between gap-2">
              <FormationLabel label={c.label} tier={c.tier} className="text-[9.5px]" />
              {c.is_demo && <DemoBadge />}
            </div>
            <h3 className="mt-1.5 font-serif text-[18px] leading-[1.22] text-ink">
              <span className="tnum mr-2 font-mono text-[11px] text-ink-3">{pad2(i + 1)}</span>
              <Link href={href} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
                {c.title}
              </Link>
            </h3>
            <MemberLine card={c} className="mt-1 line-clamp-2 text-[12.5px]" />
            {c.why_now[0] && (
              <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-snug text-ink">
                <span className="mr-1.5 font-mono text-[10px] uppercase tracking-[0.08em]">Why now</span>
                {c.why_now[0].title}
              </p>
            )}
            <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
              <span className="text-ink">{fams.join(" · ") || "No families"}</span>
              <SourcesButton claim={c.title} evidence={c.why_now} demo={c.is_demo} className="text-[10px]" />
              <span className="tnum ml-auto text-ink-3" title="Formation Score">
                Score <span className="text-ink">{Math.round(c.score)}</span>
              </span>
            </p>
          </li>
        );
      })}
    </ol>
  );
}

// ── People to meet ───────────────────────────────────────────────────────────

export function PeopleRows({ people }: { people: MeetCard[] }) {
  if (people.length === 0) return <Empty>No recommendations in this window. The weekly list refreshes from scored, dated evidence.</Empty>;
  return (
    <ol className="divide-y divide-rule">
      {people.map((m, i) => {
        const href = hrefFor("person", m.person.slug);
        const line = m.what_changed[0] ?? m.why_now[0]?.title ?? m.why_person[0] ?? null;
        return (
          <li key={m.person.id} className="grid grid-cols-[26px_minmax(0,1fr)] gap-x-2.5 py-2.5">
            <span className="tnum pt-[3px] font-mono text-[11px] text-ink-3">{pad2(i + 1)}</span>
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="truncate font-serif text-[16.5px] leading-tight text-ink">
                  <Link href={href} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
                    {m.person.name}
                  </Link>
                </h3>
                <Confidence value={m.confidence} className="shrink-0" />
              </div>
              <p className="mt-0.5 truncate font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
                {[m.area, m.affiliation_line].filter(Boolean).join(" · ")}
              </p>
              {line && <p className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-ink-2">{line}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ── Companies accelerating ───────────────────────────────────────────────────

export function CompanyRows({ cards, note }: { cards: IntelCard[]; note?: string | null }) {
  if (cards.length === 0) return <Empty>{note || "No company has a scored, explainable signal in this window."}</Empty>;
  return (
    <>
      {note && <p className="pt-2.5 text-[12.5px] leading-snug text-ink-3">{note}</p>}
      <ol className="divide-y divide-rule">
        {cards.map((c, i) => {
          const href = hrefFor(c.entity_type, c.slug);
          const why = c.why_now_sentence?.trim() || c.why_surfaced[0]?.title || c.one_liner;
          const label = c.entity_type === "person" ? "Founder Score" : "Anti Score";
          return (
            <li
              key={c.id}
              data-card
              data-href={href}
              tabIndex={0}
              className="grid grid-cols-[26px_minmax(0,1fr)_76px] items-start gap-x-3 py-2.5 outline-none focus-visible:bg-accent-wash lg:grid-cols-[26px_minmax(0,1fr)_minmax(0,280px)_76px]"
            >
              <span className="tnum pt-[3px] font-mono text-[11px] text-ink-3">{pad2(i + 1)}</span>
              <div className="min-w-0">
                <p className="flex flex-wrap items-baseline gap-x-2.5">
                  <Link
                    href={href}
                    data-action="open"
                    className="font-serif text-[17px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline"
                  >
                    {c.name}
                  </Link>
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.08em] text-ink-3">{c.kind_label}</span>
                  {c.is_demo && <DemoBadge note={c.data_note} />}
                </p>
                {why && <p className="mt-0.5 line-clamp-1 text-[12.5px] leading-snug text-ink-2">{why}</p>}
              </div>
              <div className="col-span-2 col-start-2 mt-1 min-w-0 lg:col-span-1 lg:col-start-3 lg:mt-0 lg:pt-[3px]">
                {c.collision && isCollision(c.collision) ? (
                  <CollisionLine collision={c.collision} className="text-[11.5px]" />
                ) : (
                  <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">Single source family · no collision</span>
                )}
              </div>
              <div className="col-start-3 row-start-1 text-right lg:col-start-4" title={`${label} ${c.score}`}>
                <span className="tnum block font-mono text-[15px] leading-none text-ink">{c.score}</span>
                <Delta value={c.delta_7d} size="xs" className="mt-1 block" />
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

// ── Strange signals ──────────────────────────────────────────────────────────

export function StrangeRows({ signals }: { signals: StrangeSignal[] }) {
  if (signals.length === 0)
    return <Empty>No unusual patterns detected. Detectors look for quiet velocity, lab clusters and shared-employer formations.</Empty>;
  return (
    <ol className="divide-y divide-rule">
      {signals.map((s) => (
        <li key={s.id} className="py-3">
          <p className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-2">
            <span aria-hidden className="inline-block size-[6px] bg-accent" />
            {patternLabel(s.pattern)}
            {s.is_demo && <DemoBadge className="ml-auto" />}
          </p>
          <h3 className="mt-1 font-serif text-[15.5px] leading-[1.28] text-ink">{s.headline}</h3>
          <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <EntityLinks entities={s.entities} limit={3} />
            <SourcesButton claim={s.headline} evidence={s.evidence} demo={s.is_demo} className="ml-auto text-[10px]" />
          </div>
        </li>
      ))}
    </ol>
  );
}
