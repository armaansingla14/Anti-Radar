import clsx from "clsx";

export type NumeralSize = "sm" | "md" | "lg" | "xl";

export interface ScoreNumeralProps {
  value: number | null | undefined;
  size?: NumeralSize;
  /** Burgundy numeral — reserve for the primary score on a surface. */
  accent?: boolean;
  /** Small mono caption above the number, e.g. "ANTI SCORE". */
  label?: string;
  /** Show "/100" after the number. */
  outOf?: number;
  className?: string;
}

const SIZES: Record<NumeralSize, string> = {
  sm: "text-[22px] leading-none",
  md: "text-[34px] leading-none",
  lg: "text-[56px] leading-[0.9]",
  xl: "text-[88px] leading-[0.85]",
};

/** Large serif tabular score. Null renders an em dash, never a fabricated 0. */
export function ScoreNumeral({ value, size = "md", accent, label, outOf, className }: ScoreNumeralProps) {
  const shown = value === null || value === undefined ? "—" : Math.round(value).toString();
  return (
    <div className={clsx("inline-flex flex-col", className)}>
      {label && <span className="label mb-1.5">{label}</span>}
      <span
        className={clsx(
          "tnum font-serif font-normal tracking-[-0.02em]",
          SIZES[size],
          accent && shown !== "—" ? "text-accent" : "text-ink",
        )}
        aria-label={label ? `${label}: ${shown}` : `Score ${shown}`}
      >
        {shown}
        {outOf !== undefined && shown !== "—" && (
          <span className="ml-1 align-baseline font-mono text-[11px] tracking-normal text-ink-3">/{outOf}</span>
        )}
      </span>
    </div>
  );
}
