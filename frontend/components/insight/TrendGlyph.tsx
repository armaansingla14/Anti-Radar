import clsx from "clsx";
import type { Trend } from "@/lib/types";

const GLYPH: Record<Trend, string> = { up2: "↑↑", up: "↑", flat: "→", down: "↓" };

const MOMENTUM_COPY: Record<Trend, string> = {
  up2: "Signals at least doubled vs the prior 14 days",
  up: "Signals up 25% or more vs the prior 14 days",
  flat: "Signals roughly flat vs the prior 14 days",
  down: "Signals down 25% or more vs the prior 14 days",
};

const ATTENTION_COPY: Record<Trend, string> = {
  up2: "Attention percentile up 15+ points in 14 days",
  up: "Attention percentile up 5+ points in 14 days",
  flat: "Attention roughly unchanged over 14 days (or no prior reading)",
  down: "Attention percentile down 5+ points in 14 days",
};

export interface TrendGlyphProps {
  trend: Trend;
  kind?: "momentum" | "attention";
  /** Accent the strongest rise. */
  accent?: boolean;
  className?: string;
}

/** ↑↑ / ↑ / → / ↓ with a precise tooltip of what the arrow means. */
export function TrendGlyph({ trend, kind = "momentum", accent, className }: TrendGlyphProps) {
  const title = (kind === "momentum" ? MOMENTUM_COPY : ATTENTION_COPY)[trend];
  return (
    <span
      className={clsx(
        "font-mono tracking-[-0.08em]",
        trend === "up2" && accent ? "text-accent" : trend === "down" || trend === "flat" ? "text-ink-3" : "text-ink",
        className,
      )}
      title={title}
      aria-label={title}
    >
      {GLYPH[trend]}
    </span>
  );
}
