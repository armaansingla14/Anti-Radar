"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { postFeedback } from "@/lib/api";
import { fmtDate, hrefFor, pad2 } from "@/lib/format";
import { collisionFallback, isCollision } from "@/lib/labels";
import type { AntiScore, EvidenceItem, FeedbackLabel, IntelCard as IntelCardData } from "@/lib/types";
import { AttentionGapMini } from "./AttentionGapPlot";
import { DemoBadge, EpistemicBadge, NewBadge } from "./Badges";
import { CollisionLine } from "./CollisionStrip";
import { Confidence } from "./Confidence";
import { DataAge } from "./DataAge";
import { Delta } from "./Delta";
import { FeedbackMenu } from "./FeedbackMenu";
import { SourceGlyph } from "./SourceLink";
import { SourcesButton } from "./SourceDrawer";
import { Sparkline } from "./Sparkline";
import { ThesisTag } from "./ThesisTag";
import { useToast } from "./Toast";
import { WatchButton } from "./WatchButton";
import { WhyDrawer, WhyDrawerLoader } from "./WhyDrawer";

export interface IntelCardProps {
  card: IntelCardData;
  /** Full score breakdown, if already loaded — the Why drawer then skips fetching. */
  score?: AntiScore | null;
  /** Detail route; defaults to /companies/{slug} or /founders/{slug}. */
  href?: string;
  /** Replace the built-in Why drawer. */
  onWhy?: (card: IntelCardData) => void;
  /** Replace the built-in watchlist call. */
  onWatch?: (card: IntelCardData, next: boolean) => void | Promise<void>;
  /** Replace the default (navigate to `${href}?investigate=1`). */
  onInvestigate?: (card: IntelCardData) => void;
  /** Replace the built-in POST /api/feedback. */
  onFeedback?: (card: IntelCardData, label: FeedbackLabel) => void | Promise<void>;
  /** Show a "Dismiss" action; the caller removes the card and records feedback. */
  onDismiss?: (card: IntelCardData) => void;
  /** Max evidence bullets under "Why it surfaced". */
  evidenceLimit?: number;
  className?: string;
}

/** why_surfaced first, then collision evidence not already listed. */
function mergeEvidence(a: EvidenceItem[], b: EvidenceItem[]): EvidenceItem[] {
  const seen = new Set(a.map((e) => e.signal_id ?? `${e.source_url}|${e.title}`));
  return [...a, ...b.filter((e) => !seen.has(e.signal_id ?? `${e.source_url}|${e.title}`))];
}

/** Evidence as dated bullets: date · source · claim. Undated (computed) items say so. */
export function DatedBullets({ items, limit = 4 }: { items: EvidenceItem[]; limit?: number }) {
  if (items.length === 0) return <p className="text-[13px] text-ink-3">No dated evidence recorded.</p>;
  const shown = items.slice(0, limit);
  return (
    <ul className="space-y-1">
      {shown.map((e, i) => (
        <li key={e.signal_id ?? `${e.title}-${i}`} className="grid grid-cols-[52px_26px_1fr] items-baseline gap-x-2">
          <span className="tnum font-mono text-[10.5px] uppercase text-ink-3" title={e.event_at ? fmtDate(e.event_at) : "Computed from stored history"}>
            {e.event_at ? fmtDate(e.event_at, { year: false }) : "—"}
          </span>
          <SourceGlyph sourceType={e.source_type} className="translate-y-[1px]" />
          <span className="text-[13px] leading-snug text-ink">{e.title}</span>
        </li>
      ))}
      {items.length > limit && <li className="label pl-[86px] text-ink-3">+ {items.length - limit} more</li>}
    </ul>
  );
}

const action =
  "font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-ink focus-visible:text-ink";

/** The numbered editorial card used on Radar, Daily and Brief feeds. */
export function IntelCard({
  card,
  score,
  href,
  onWhy,
  onWatch,
  onInvestigate,
  onFeedback,
  onDismiss,
  evidenceLimit = 4,
  className,
}: IntelCardProps) {
  const [whyOpen, setWhyOpen] = useState(false);
  const toast = useToast();
  const target = href ?? hrefFor(card.entity_type, card.slug);
  const isPerson = card.entity_type === "person";
  const canInvestigate = card.entity_type === "company" || card.entity_type === "person";
  const scoreLabel = isPerson ? "Founder Score" : "Anti Score";

  const openWhy = () => (onWhy ? onWhy(card) : setWhyOpen(true));
  const feedback = async (label: FeedbackLabel) => {
    try {
      if (onFeedback) await onFeedback(card, label);
      else await postFeedback({ entity_type: card.entity_type, entity_id: card.id, label });
      toast("Feedback recorded — it will adjust future rankings");
    } catch (err) {
      toast("Could not record feedback", { tone: "error" });
      throw err;
    }
  };

  const evidence = mergeEvidence(card.why_surfaced, card.collision?.evidence ?? []);
  const why = card.why_now_sentence?.trim();
  const hasAge = Object.keys(card.data_age?.families ?? {}).length > 0;

  return (
    <article
      data-card
      data-href={target}
      tabIndex={0}
      aria-labelledby={`card-${card.id}-name`}
      className={clsx("relative grid animate-rise grid-cols-12 gap-x-8 gap-y-5 border-t border-ink pb-7 pt-4 focus-within:z-20", className)}
    >
      {/* ── left: ENTITY → WHY NOW → RECENT CHANGE → INDEPENDENT SIGNALS ── */}
      <div className="col-span-12 min-w-0 md:col-span-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="tnum font-mono text-[12px] text-ink">{pad2(card.rank)}</span>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label">{card.kind_label}</span>
          {card.is_new && <NewBadge />}
          {card.is_demo && <DemoBadge note={card.data_note} />}
          <Confidence value={card.confidence} className="ml-auto" />
        </div>

        <h3 id={`card-${card.id}-name`} className="mt-2.5 font-serif text-[28px] leading-[1.08] tracking-[-0.01em] text-ink">
          <Link href={target} className="decoration-accent decoration-1 underline-offset-4 hover:underline" tabIndex={-1}>
            {card.name}
          </Link>
        </h3>
        {card.one_liner && <p className="mt-1.5 max-w-[62ch] text-[14.5px] leading-relaxed text-ink-2">{card.one_liner}</p>}

        <section className="mt-5" aria-label="Why now">
          <h4 className="label mb-2 text-ink">Why now</h4>
          {why && <p className="mb-2.5 max-w-[60ch] font-serif text-[18px] leading-[1.3] text-ink">{why}</p>}
          <DatedBullets items={card.why_surfaced} limit={evidenceLimit} />
        </section>

        {card.recent_change && (
          <p className="mt-4 grid grid-cols-[auto_1fr] items-baseline gap-x-3 text-[13.5px] leading-snug text-ink">
            <span className="label text-ink">Recent change</span>
            <span>{card.recent_change}</span>
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-rule pt-3">
          <span className="label text-ink">Independent signals</span>
          {card.collision && isCollision(card.collision) ? (
            <CollisionLine collision={card.collision} />
          ) : (
            <span className="text-[12.5px] text-ink-3">{collisionFallback(card.collision) ? "Single source family — no collision yet" : "None yet"}</span>
          )}
          <SourcesButton claim={`Why ${card.name} surfaced`} evidence={evidence} count={card.sources_count} demo={card.is_demo} className="ml-auto" />
        </div>

        {card.why_anti.length > 0 && (
          <div className="mt-4">
            <h4 className="label mb-1.5 flex items-center gap-2 text-ink">
              Why Anti Fund might care <EpistemicBadge value={card.why_anti_epistemic} />
            </h4>
            <ul className="space-y-1">
              {card.why_anti.map((line, i) => (
                <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink-2">
                  <span aria-hidden className="text-ink-3">→</span>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}

        {card.thesis_tags.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2" aria-label="Thesis tags">
            {card.thesis_tags.map((t) => (
              <ThesisTag key={t.key} tag={t} />
            ))}
          </div>
        )}
      </div>

      {/* ── right: the score, deliberately secondary ── */}
      <aside className="col-span-12 flex flex-col gap-3.5 md:col-span-4 md:border-l md:border-rule md:pl-7" aria-label={`${scoreLabel} ${card.score}`}>
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="label mb-1">{scoreLabel}</p>
            <p className="flex items-baseline gap-3">
              <span className="tnum font-serif text-[32px] leading-none tracking-[-0.02em] text-ink">{card.score}</span>
              <button type="button" className={action} data-action="why" onClick={openWhy} aria-haspopup="dialog">
                Why {card.score}?
              </button>
            </p>
          </div>
          <div className="flex flex-col items-end gap-1 pb-0.5">
            <Delta value={card.delta_7d} period="this week" size="xs" />
            {card.delta_30d !== null && <Delta value={card.delta_30d} period="30d" size="xs" accentAt={Infinity} />}
          </div>
        </div>
        {card.sparkline.length > 1 && <Sparkline values={card.sparkline} width={200} height={30} label={card.sparkline_label} />}
        {card.attention_gap && <AttentionGapMini gap={card.attention_gap} />}
        {card.first_seen_at && <p className="label text-ink-3">First seen {fmtDate(card.first_seen_at)}</p>}
        {hasAge && <DataAge age={card.data_age} now={new Date()} entityType={card.entity_type === "person" ? "person" : "company"} />}
      </aside>

      {/* ── actions ── */}
      <div className="col-span-12 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-rule pt-3">
        <Link href={target} className={clsx(action, "text-ink")} data-action="open">
          Open report →
        </Link>
        {card.entity_type !== "paper" && (
          <WatchButton
            entityType={card.entity_type}
            entityId={card.id}
            watched={card.watched}
            onToggle={onWatch ? (next) => onWatch(card, next) : undefined}
          />
        )}
        {canInvestigate &&
          (onInvestigate ? (
            <button type="button" className={action} data-action="investigate" onClick={() => onInvestigate(card)}>
              Investigate
            </button>
          ) : (
            <Link href={`${target}?investigate=1`} className={action} data-action="investigate">
              Investigate
            </Link>
          ))}
        <FeedbackMenu onSelect={feedback} />
        {onDismiss && (
          <button type="button" className={action} data-action="dismiss" onClick={() => onDismiss(card)}>
            Dismiss
          </button>
        )}
        {card.data_note && <span className="ml-auto font-mono text-[10px] text-ink-3">{card.data_note}</span>}
      </div>

      {!onWhy &&
        (score !== undefined || !canInvestigate ? (
          <WhyDrawer open={whyOpen} onClose={() => setWhyOpen(false)} score={score ?? null} entityName={card.name} scoreLabel={scoreLabel} isDemo={card.is_demo} />
        ) : (
          <WhyDrawerLoader
            open={whyOpen}
            onClose={() => setWhyOpen(false)}
            entityType={isPerson ? "person" : "company"}
            idOrSlug={card.slug}
            entityName={card.name}
            isDemo={card.is_demo}
          />
        ))}
    </article>
  );
}
