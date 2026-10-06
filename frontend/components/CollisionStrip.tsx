import clsx from "clsx";
import { daysBetween, fmtDate, fmtNum, parseDate, sourceLabel } from "@/lib/format";
import type { Collision } from "@/lib/types";

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
        <p className="text-[13.5px] text-ink">{collision.summary}</p>
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
