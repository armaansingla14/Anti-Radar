import clsx from "clsx";

export interface DeltaProps {
  value: number | null | undefined;
  /** Trailing period text, e.g. "this week", "30d". Omit for bare delta. */
  period?: string;
  /** Moves at or above this magnitude use the accent. */
  accentAt?: number;
  size?: "xs" | "sm" | "md";
  digits?: number;
  className?: string;
}

const SIZES = { xs: "text-[10.5px]", sm: "text-[12px]", md: "text-[14px]" } as const;

/**
 * Score change: "↑ 18 this week". Rises in ink (accent for large moves),
 * declines muted, missing history explicitly labelled.
 */
export function Delta({ value, period, accentAt = 10, size = "sm", digits = 0, className }: DeltaProps) {
  const base = clsx("tnum font-mono whitespace-nowrap", SIZES[size], className);
  if (value === null || value === undefined || Number.isNaN(value)) {
    return (
      <span className={clsx(base, "text-ink-3")} title="No prior score to compare against">
        — no prior
      </span>
    );
  }
  const r = Number(value.toFixed(digits));
  const glyph = r > 0 ? "↑" : r < 0 ? "↓" : "→";
  const tone = r > 0 ? (r >= accentAt ? "text-accent" : "text-ink") : "text-ink-2";
  const magnitude = Math.abs(r).toLocaleString("en-US", { maximumFractionDigits: digits });
  const words = r > 0 ? "up" : r < 0 ? "down" : "unchanged";
  return (
    <span className={clsx(base, tone)} aria-label={`${words} ${Math.abs(r)}${period ? ` ${period}` : ""}`}>
      <span aria-hidden>
        {glyph} {magnitude}
        {period && <span className="text-ink-3"> {period}</span>}
      </span>
    </span>
  );
}
