import clsx from "clsx";
import { fmtNum } from "@/lib/format";

export interface ScoreBarProps {
  value: number;
  max: number;
  label?: string;
  /** Render "12.4 / 25" at the right. */
  showValue?: boolean;
  accent?: boolean;
  className?: string;
}

/** Thin component bar (value / max) on a hairline track. */
export function ScoreBar({ value, max, label, showValue, accent, className }: ScoreBarProps) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={clsx("w-full", className)}>
      {(label || showValue) && (
        <div className="mb-1 flex items-baseline justify-between gap-3">
          {label && <span className="text-[13px] text-ink">{label}</span>}
          {showValue && (
            <span className="tnum font-mono text-[11px] text-ink-2">
              {fmtNum(value, 1)} <span className="text-ink-3">/ {fmtNum(max, 0)}</span>
            </span>
          )}
        </div>
      )}
      <div
        className="relative h-[3px] w-full bg-rule"
        role="meter"
        aria-label={label ?? "score component"}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={Number(value.toFixed(2))}
      >
        <div className={clsx("absolute inset-y-0 left-0", accent ? "bg-accent" : "bg-ink")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
