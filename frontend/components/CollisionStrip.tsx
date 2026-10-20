import clsx from "clsx";
import { daysBetween, fmtDate, fmtNum, parseDate, sourceLabel } from "@/lib/format";
import { collisionView, type StrengthWord } from "@/lib/labels";
import type { Collision } from "@/lib/types";

const STRENGTH_CLS: Record<StrengthWord, string> = {
  HIGH: "border border-ink text-ink",
  MEDIUM: "border border-rule-strong text-ink-2",
  LOW: "border border-dotted border-ink-3 text-ink-3",
};

/** "HIGH" / "MEDIUM" / "LOW" collision-strength chip. */
export function StrengthWordBadge({ value, className }: { value: StrengthWord; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-1.5 py-[1px] font-mono text-[9.5px] uppercase leading-[14px] tracking-[0.08em] whitespace-nowrap",
        STRENGTH_CLS[value],
        className,
      )}
      title={`${value.charAt(0)}${value.slice(1).toLowerCase()} collision strength — independent source families converging in a short window`}
    >
      {value}
    </span>
  );
}

export interface CollisionLineProps {
  collision: Pick<Collision, "families" | "summary" | "strength" | "family_labels" | "sentence">;
  className?: string;
}

/** One line: STRENGTH · GitHub · Research — "4 independent sources within 17 days". */
export function CollisionLine({ collision, className }: CollisionLineProps) {
  const v = collisionView(collision);
  return (
    <p className={clsx("flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] leading-snug text-ink-2", className)}>
      {v.strength && <StrengthWordBadge value={v.strength} />}
      {v.families.length > 0 && (
        <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink">{v.families.join(" · ")}</span>
      )}
      <span>{v.sentence}</span>
    </p>
  );
}

export interface CollisionStripProps {
  collision: Collision;
  /** End of the window (defaults to the latest evidence date). */
  asOf?: string | null;
  className?: string;
}

/** Monochrome shape per source family so identity never depends on colour. */
const FAMILY_GLYPHS = ["●", "◆", "▲", "■", "▼", "✕", "○", "◇"];

export function familyGlyph(families: string[], family: string): string {
  const i = families.indexOf(family);
  return FAMILY_GLYPHS[(i < 0 ? families.length : i) % FAMILY_GLYPHS.length];
}

/** Signal collision: independent source families converging inside the window. */
export function CollisionStrip({ collision, asOf, className }: CollisionStripProps) {
  const families = collision.families;
  const dated = collision.evidence
    .map((e) => ({ e, d: parseDate(e.event_at) }))
    .filter((x): x is { e: (typeof collision.evidence)[number]; d: Date } => x.d !== null);
  const end = parseDate(asOf) ?? (dated.length ? new Date(Math.max(...dated.map((x) => x.d.getTime()))) : null);
  const windowDays = Math.max(1, collision.window_days);
  const start = end ? new Date(end.getTime() - windowDays * 86_400_000) : null;

  return (
    <div className={clsx("space-y-3", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <CollisionLine collision={collision} className="text-[13.5px] text-ink" />
        <p className="label tnum">
          Collision <span className="text-ink">{fmtNum(collision.score, 2)}</span>
          {collision.span_days !== null && <> · span {fmtNum(collision.span_days, 0)}d</>} · {collision.signal_count} signals
        </p>
      </div>

      {end && start && (
        <div className="relative h-11" role="img" aria-label={`${dated.length} dated signals across ${families.length} source families within ${windowDays} days`}>
          <div className="absolute inset-x-0 top-[18px] h-px bg-ink" />
          <div className="absolute left-0 top-[13px] h-[11px] w-px bg-ink" />
          <div className="absolute right-0 top-[13px] h-[11px] w-px bg-ink" />
          {dated.map(({ e, d }, i) => {
            const pos = Math.max(0, Math.min(100, (daysBetween(start, d) / windowDays) * 100));
            const fam = e.source_type ?? "unknown";
            return (
              <span
                key={`${e.signal_id ?? i}`}
                className="absolute top-[10px] -translate-x-1/2 font-mono text-[11px] leading-none text-ink"
                style={{ left: `${pos}%` }}
                title={`${fmtDate(d)} · ${sourceLabel(fam)} · ${e.title}`}
              >
                <span className="bg-paper px-[1px]">{familyGlyph(families, fam)}</span>
              </span>
            );
          })}
          <span className="tnum absolute left-0 top-[28px] font-mono text-[9.5px] uppercase text-ink-3">{fmtDate(start, { year: false })}</span>
          <span className="tnum absolute right-0 top-[28px] font-mono text-[9.5px] uppercase text-ink-3">{fmtDate(end, { year: false })}</span>
          <span className="absolute left-1/2 top-[28px] -translate-x-1/2 font-mono text-[9.5px] uppercase text-ink-3">{windowDays}-day window</span>
        </div>
      )}

      <ul className="flex flex-wrap gap-x-4 gap-y-1" aria-label="Source families">
        {families.map((f) => (
          <li key={f} className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
            <span aria-hidden className="text-ink">{familyGlyph(families, f)}</span>
            {sourceLabel(f)}
          </li>
        ))}
      </ul>
    </div>
  );
}
