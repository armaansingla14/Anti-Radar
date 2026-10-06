"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { postFeedback } from "@/lib/api";
import { fmtDate, hrefFor, pad2 } from "@/lib/format";
import type { AntiScore, FeedbackLabel, IntelCard as IntelCardData } from "@/lib/types";
import { AttentionGapMini } from "./AttentionGapPlot";
import { DemoBadge, EpistemicBadge, NewBadge } from "./Badges";
import { Delta } from "./Delta";
import { EvidenceList } from "./EvidenceList";
import { FeedbackMenu } from "./FeedbackMenu";
import { ScoreNumeral } from "./ScoreNumeral";
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
  /** Max evidence bullets under "Why it surfaced". */
  evidenceLimit?: number;
  className?: string;
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

  return (
    <article
      data-card
      data-href={target}
      tabIndex={0}
      aria-labelledby={`card-${card.id}-name`}
      className={clsx("grid animate-rise grid-cols-12 gap-x-8 gap-y-5 border-t border-ink pb-8 pt-4", className)}
    >
      {/* ── left: editorial body ── */}
      <div className="col-span-12 min-w-0 md:col-span-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="tnum font-mono text-[12px] text-ink">{pad2(card.rank)}</span>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label">{card.kind_label}</span>
          {card.is_new && <NewBadge />}
          {card.is_demo && <DemoBadge note={card.data_note} />}
        </div>

        <h3 id={`card-${card.id}-name`} className="mt-3 font-serif text-[30px] leading-[1.08] tracking-[-0.01em] text-ink">
          <Link href={target} className="decoration-accent decoration-1 underline-offset-4 hover:underline" tabIndex={-1}>
            {card.name}
          </Link>
        </h3>
        {card.one_liner && <p className="mt-2 max-w-[62ch] text-[15px] leading-relaxed text-ink-2">{card.one_liner}</p>}

        <div className="mt-6 grid gap-x-8 gap-y-6 lg:grid-cols-2">
          <div>
            <h4 className="label mb-3 text-ink">Why it surfaced</h4>
            <EvidenceList items={card.why_surfaced} limit={evidenceLimit} dense />
          </div>
          <div>
            <h4 className="label mb-3 flex items-center gap-2 text-ink">
              Why Anti Fund might care <EpistemicBadge value={card.why_anti_epistemic} />
            </h4>
            {card.why_anti.length > 0 ? (
              <ul className="space-y-1.5">
                {card.why_anti.map((line, i) => (
                  <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink">
                    <span aria-hidden className="text-ink-3">→</span>
                    {line}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-ink-3">No thesis rationale recorded.</p>
            )}
          </div>
        </div>

        {card.thesis_tags.length > 0 && (
          <div className="mt-5 flex flex-wrap gap-x-4 gap-y-2" aria-label="Thesis tags">
            {card.thesis_tags.map((t) => (
              <ThesisTag key={t.key} tag={t} />
            ))}
          </div>
        )}
      </div>

      {/* ── right: score column ── */}
      <aside className="col-span-12 flex flex-col gap-4 md:col-span-4 md:border-l md:border-rule md:pl-7" aria-label={`${scoreLabel} ${card.score}`}>
        <div className="flex items-end justify-between gap-4">
          <ScoreNumeral value={card.score} size="lg" accent label={scoreLabel} />
          <div className="flex flex-col items-end gap-1 pb-1">
            <Delta value={card.delta_7d} period="this week" />
            {card.delta_30d !== null && <Delta value={card.delta_30d} period="30d" size="xs" accentAt={Infinity} />}
          </div>
        </div>
        {card.sparkline.length > 1 && <Sparkline values={card.sparkline} width={200} height={34} label={card.sparkline_label} />}
        {card.attention_gap && <AttentionGapMini gap={card.attention_gap} />}
        {card.collision && (
          <p className="font-mono text-[10.5px] leading-snug text-ink-2">
            <span className="text-ink">{card.collision.families.length}</span> source families · {card.collision.summary}
          </p>
        )}
        {card.first_seen_at && <p className="label text-ink-3">First seen {fmtDate(card.first_seen_at)}</p>}
      </aside>

      {/* ── actions ── */}
      <div className="col-span-12 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-rule pt-3">
        <Link href={target} className={clsx(action, "text-ink")} data-action="open">
          Open report →
        </Link>
        <button type="button" className={action} data-action="why" onClick={openWhy} aria-haspopup="dialog">
          Why {card.score}?
        </button>
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
        {card.data_note && <span className="ml-auto font-mono text-[10px] text-ink-3">{card.data_note}</span>}
      </div>

      {!onWhy &&
        (score !== undefined || !canInvestigate ? (
          <WhyDrawer open={whyOpen} onClose={() => setWhyOpen(false)} score={score ?? null} entityName={card.name} scoreLabel={scoreLabel} />
        ) : (
          <WhyDrawerLoader
            open={whyOpen}
            onClose={() => setWhyOpen(false)}
            entityType={isPerson ? "person" : "company"}
            idOrSlug={card.slug}
            entityName={card.name}
          />
        ))}
    </article>
  );
}
