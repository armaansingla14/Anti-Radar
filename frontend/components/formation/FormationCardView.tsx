import clsx from "clsx";
import Link from "next/link";
import { DemoBadge } from "@/components/Badges";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { WatchButton } from "@/components/WatchButton";
import { pad2 } from "@/lib/format";
import type { FormationCard } from "@/lib/types";
import { FormationWhy } from "./FormationWhy";
import { FormationLabel, ThemeLine } from "./Labels";
import { formationCaveat, headlineFacts, memberEntries, memberNames, memberSummary, watchTarget } from "./logic";
import { SignalStrip } from "./SignalStrip";
import { WhyNow } from "./WhyNow";

export const formationHref = (c: Pick<FormationCard, "slug" | "id">) => `/formation/${encodeURIComponent(c.slug || c.id)}`;

/**
 * Member line: "1 person · Kevin Zakka — google-deepmind/mujoco_menagerie · 149 commits (verified)".
 * Members with verified lab commits come first with their pedigree; then names;
 * profile affiliations stay labelled self-reported.
 */
export function MemberLine({ card, className }: { card: FormationCard; className?: string }) {
  const { count, previously } = memberSummary(card);
  const { shown, rest } = memberEntries(card.members);
  const anyPedigree = shown.some((m) => m.pedigree);
  const showNames = shown.length > 0 && (anyPedigree || card.title !== memberNames(card.members));
  return (
    <p className={clsx("text-[13.5px] leading-snug text-ink-2", className)}>
      <span className="tnum font-mono text-[11px] uppercase tracking-[0.06em] text-ink">{count}</span>
      {showNames && (
        <>
          <span aria-hidden className="mx-2 text-ink-3">·</span>
          {shown.map((m, i) => (
            <span key={`${m.name}-${i}`}>
              {i > 0 && (m.pedigree || shown[i - 1].pedigree ? "; " : i === shown.length - 1 && !rest ? " and " : ", ")}
              <span className={m.pedigree ? "text-ink" : undefined}>{m.name}</span>
              {m.pedigree && (
                <span className="text-ink-2">
                  {" — "}
                  <span className="font-mono text-[11.5px] tracking-[0.01em]">{m.pedigree}</span>
                </span>
              )}
            </span>
          ))}
          {rest > 0 && ` and ${rest} ${rest === 1 ? "other" : "others"}`}
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

/** Headline facts: 2–4 verified / measured facts, one mono glyph per family. */
export function HeadlineFacts({ card, className }: { card: FormationCard; className?: string }) {
  const facts = headlineFacts(card);
  if (facts.length === 0) return null;
  return (
    <ul aria-label="Key facts" className={clsx("space-y-1", className)}>
      {facts.map((f, i) => (
        <li key={`${f.kind}-${i}`} className="grid grid-cols-[18px_1fr] items-baseline gap-x-2">
          <span aria-hidden className={clsx("font-mono text-[12.5px] leading-none", f.kind === "pedigree" ? "text-accent" : "text-ink-2")}>
            {f.glyph}
          </span>
          <span className="tnum text-[14.5px] leading-snug text-ink">{f.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Formation candidate card. Hierarchy: label + theme → title → headline facts →
 * who (with verified pedigree) → WHY NOW → signal strip; the Formation Score
 * sits smaller on the right and supports the story.
 * Carries data-card/data-href for J/K/O, data-action="why" for Y, "watch" for W.
 */
export function FormationCardView({ card, rank, className }: { card: FormationCard; rank?: number; className?: string }) {
  const href = formationHref(card);
  const watch = watchTarget(card.anchor);
  return (
    <article
      data-card
      data-demo="formation-card"
      data-demo-slug={card.slug}
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
        {formationCaveat(card) && (
          <p className="mt-1.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">{formationCaveat(card)}</p>
        )}
        <HeadlineFacts card={card} className="mt-3" />
        <MemberLine card={card} className="mt-3" />

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
          data-demo="formation-open"
          className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink transition-colors hover:text-accent"
        >
          View formation report →
        </Link>
        {watch && <WatchButton entityType={watch.entityType} entityId={watch.entityId} watched={false} />}
      </div>
    </article>
  );
}
