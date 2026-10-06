import clsx from "clsx";
import Link from "next/link";
import { DemoBadge, NewBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { SourceGlyph } from "@/components/SourceLink";
import { WatchButton } from "@/components/WatchButton";
import { hrefFor, pad2 } from "@/lib/format";
import type { IntelCard } from "@/lib/types";

const action =
  "font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-ink focus-visible:text-ink";

export interface DigestRowProps {
  card: IntelCard;
  index: number;
  /** Evidence lines to show. */
  evidence?: number;
}

/**
 * Dense, keyboard-navigable digest row: rank · identity · what changed · score.
 * Carries data-card so J/K, O, W and I work on it.
 */
export function DigestRow({ card, index, evidence = 2 }: DigestRowProps) {
  const href = hrefFor(card.entity_type, card.slug);
  const canAct = card.entity_type === "company" || card.entity_type === "person";
  return (
    <article
      data-card
      data-href={href}
      tabIndex={0}
      aria-labelledby={`digest-${card.id}`}
      className="grid grid-cols-[34px_minmax(0,1fr)_92px] gap-x-4 border-t border-rule py-4 md:grid-cols-[40px_minmax(0,1fr)_minmax(0,1fr)_104px] md:gap-x-6"
    >
      <span className="tnum pt-[3px] font-mono text-[12px] text-ink">{pad2(index)}</span>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="label">{card.kind_label}</span>
          {card.is_new && <NewBadge />}
          {card.is_demo && <DemoBadge note={card.data_note} />}
        </div>
        <h3 id={`digest-${card.id}`} className="mt-1 font-serif text-[21px] leading-tight text-ink">
          <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {card.name}
          </Link>
        </h3>
        {card.one_liner && <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-ink-2">{card.one_liner}</p>}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <Link href={href} className={clsx(action, "text-ink")} data-action="open">
            Open →
          </Link>
          {canAct && <WatchButton entityType={card.entity_type as "company" | "person"} entityId={card.id} watched={card.watched} />}
          {canAct && (
            <Link href={`${href}?investigate=1`} className={action} data-action="investigate">
              Investigate
            </Link>
          )}
        </div>
      </div>

      <ul className="col-span-2 col-start-2 row-start-2 mt-3 space-y-1.5 md:col-span-1 md:col-start-3 md:row-start-1 md:mt-0 md:pt-[22px]">
        {card.why_surfaced.slice(0, evidence).map((e, i) => (
          <li key={e.signal_id ?? i} className="grid grid-cols-[24px_1fr] gap-2 text-[12.5px] leading-snug text-ink">
            <SourceGlyph sourceType={e.source_type} className="mt-[1px]" />
            <span className="min-w-0">{e.title}</span>
          </li>
        ))}
        {card.why_surfaced.length === 0 && <li className="text-[12.5px] text-ink-3">No evidence lines recorded.</li>}
      </ul>

      <div className="col-start-3 row-start-1 flex flex-col items-end gap-1 text-right md:col-start-4">
        <span className="tnum font-serif text-[34px] leading-none tracking-[-0.02em] text-ink" aria-label={`Score ${card.score}`}>
          {card.score}
        </span>
        <Delta value={card.delta_7d} period="7d" size="xs" />
      </div>
    </article>
  );
}
