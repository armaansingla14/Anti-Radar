import clsx from "clsx";
import Link from "next/link";
import { DemoBadge } from "@/components/Badges";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { WatchButton } from "@/components/WatchButton";
import { pad2 } from "@/lib/format";
import type { FormationCard } from "@/lib/types";
import { FormationWhy } from "./FormationWhy";
import { FormationLabel, ThemeLine } from "./Labels";
import { memberNames, memberSummary, watchTarget } from "./logic";
import { SignalStrip } from "./SignalStrip";
import { WhyNow } from "./WhyNow";

export const formationHref = (c: Pick<FormationCard, "slug" | "id">) => `/formation/${encodeURIComponent(c.slug || c.id)}`;

/** Member line: "3 people · Ana Chen, Raj Patel and Li Wei · previously NVIDIA, CMU (self-reported)". */
export function MemberLine({ card, className }: { card: FormationCard; className?: string }) {
  const { count, previously } = memberSummary(card);
  const names = memberNames(card.members);
  return (
    <p className={clsx("text-[13.5px] leading-snug text-ink-2", className)}>
      <span className="tnum font-mono text-[11px] uppercase tracking-[0.06em] text-ink">{count}</span>
      {names && card.title !== names && (
        <>
          <span aria-hidden className="mx-2 text-ink-3">·</span>
          {names}
        </>
      )}
      {previously.length > 0 && (
        <>
          <span aria-hidden className="mx-2 text-ink-3">·</span>
          previously <span className="text-ink">{previously.join(", ")}</span>{" "}
          {card.previous_affiliations_status !== "VERIFIED" && (
            <span
              className="whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3"
              title={card.previous_affiliations_note ?? "Self-reported profile fields — not verified"}
            >
              (self-reported)
            </span>
          )}
        </>
      )}
    </p>
  );
}

/**
 * Formation candidate card. Hierarchy: label → title → who → WHY NOW → signal
 * strip; the Formation Score sits smaller on the right and supports the story.
 * Carries data-card/data-href for J/K/O, data-action="why" for Y, "watch" for W.
 */
export function FormationCardView({ card, rank, className }: { card: FormationCard; rank?: number; className?: string }) {
  const href = formationHref(card);
  const watch = watchTarget(card.anchor);
  return (
    <article
      data-card
      data-href={href}
      tabIndex={0}
      aria-labelledby={`fc-${card.id}`}
      className={clsx("grid animate-rise grid-cols-12 gap-x-8 gap-y-4 border-t border-ink pb-7 pt-4", className)}
    >
      <div className="col-span-12 min-w-0 md:col-span-9">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          {rank !== undefined && <span className="tnum font-mono text-[12px] text-ink">{pad2(rank)}</span>}
          {rank !== undefined && <span aria-hidden className="h-px w-5 bg-ink-3" />}
          <FormationLabel label={card.label} tier={card.tier} />
          <ThemeLine theme={card.theme_label} subtopic={card.subtopic_label} />
          {card.is_demo && <DemoBadge />}
        </div>
        <h3 id={`fc-${card.id}`} className="mt-2.5 font-serif text-[28px] leading-[1.1] tracking-[-0.01em] text-ink">
          <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {card.title}
          </Link>
        </h3>
        <MemberLine card={card} className="mt-1.5" />

        <div className="mt-5">
          <h4 className="label mb-2.5 text-ink">Why now</h4>
          <WhyNow items={card.why_now} limit={4} demo={card.is_demo} />
        </div>
        <SignalStrip card={card} className="mt-5 border-t border-rule pt-3" />
      </div>

      <aside
        className="col-span-12 flex items-end justify-between gap-4 md:col-span-3 md:flex-col md:items-end md:justify-start md:border-l md:border-rule md:pl-6"
        aria-label={`Formation Score ${card.score}`}
      >
        <ScoreNumeral value={card.score} size="md" label="Formation Score" className="md:items-end" />
        <FormationWhy card={card} />
      </aside>

      <div className="col-span-12 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-rule pt-3">
        <Link
          href={href}
          data-action="open"
          className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink transition-colors hover:text-accent"
        >
          View formation report →
        </Link>
        {watch && <WatchButton entityType={watch.entityType} entityId={watch.entityId} watched={false} />}
      </div>
    </article>
  );
}
