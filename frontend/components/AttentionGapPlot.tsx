import clsx from "clsx";
import { fmtNum, fmtSigned } from "@/lib/format";
import type { AttentionGap } from "@/lib/types";

export interface AttentionGapPlotProps {
  gap: AttentionGap;
  /** Hide the source availability list. */
  hideSources?: boolean;
  className?: string;
}

const COVERAGE_COPY: Record<AttentionGap["coverage"], string> = {
  good: "Good coverage",
  partial: "Partial coverage",
  low: "Low coverage — treat the gap as indicative only",
};

function Row({ label, value, tone }: { label: string; value: number; tone: "ink" | "muted" }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="grid grid-cols-[92px_1fr_40px] items-center gap-3">
      <span className="label">{label}</span>
      <div className="relative h-[10px] border-l border-ink-3">
        <div className="absolute inset-y-[4px] left-0 right-0 bg-rule" />
        <div
          className={clsx("absolute inset-y-0 left-0", tone === "ink" ? "bg-ink" : "hatch border-0 bg-panel")}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="tnum text-right font-mono text-[11px] text-ink">{fmtNum(value, 0)}</span>
    </div>
  );
}

/**
 * Technical signal vs. public attention (both cohort percentiles, 0–100),
 * the gap between them, and which attention sources were actually measured.
 */
export function AttentionGapPlot({ gap, hideSources, className }: AttentionGapPlotProps) {
  const lo = Math.min(gap.technical, gap.attention);
  const hi = Math.max(gap.technical, gap.attention);
  return (
    <div className={clsx("space-y-3", className)}>
      <div className="space-y-2">
        <Row label="Technical" value={gap.technical} tone="ink" />
        <Row label="Attention" value={gap.attention} tone="muted" />
        {/* gap bracket on the same 0–100 scale */}
        <div className="grid grid-cols-[92px_1fr_40px] items-center gap-3">
          <span />
          <div className="relative h-[14px]">
            <div
              className={clsx("absolute top-[6px] h-px", gap.flagged ? "bg-accent" : "bg-ink-3")}
              style={{ left: `${lo}%`, width: `${Math.max(0, hi - lo)}%` }}
            />
            <div className={clsx("absolute top-[2px] h-[9px] w-px", gap.flagged ? "bg-accent" : "bg-ink-3")} style={{ left: `${lo}%` }} />
            <div className={clsx("absolute top-[2px] h-[9px] w-px", gap.flagged ? "bg-accent" : "bg-ink-3")} style={{ left: `${hi}%` }} />
          </div>
          <span />
        </div>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-2 border-t border-rule pt-2">
        <p className={clsx("font-mono text-[11px] uppercase tracking-[0.08em]", gap.flagged ? "text-accent" : "text-ink")}>
          {gap.flagged ? "◆ Attention gap " : "Gap "}
          <span className="tnum">{fmtSigned(gap.gap, 0)} pts</span>
        </p>
        <p className={clsx("label", gap.coverage === "low" ? "text-accent" : "text-ink-3")}>{COVERAGE_COPY[gap.coverage]}</p>
      </div>
      <p className="text-[12.5px] leading-snug text-ink-2">
        {gap.gap > 0
          ? `Technical activity sits at the ${ordinal(gap.technical)} percentile of the cohort; public attention at the ${ordinal(gap.attention)}.`
          : `Public attention is at or ahead of technical activity relative to the cohort.`}
      </p>

      {!hideSources && gap.sources.length > 0 && (
        <ul className="divide-y divide-rule border-y border-rule">
          {gap.sources.map((s) => (
            <li key={s.key} className="grid grid-cols-[14px_1fr_auto] items-baseline gap-2 py-1.5 text-[12.5px]">
              <span aria-hidden className={s.available ? "text-ink" : "text-ink-3"}>
                {s.available ? "●" : "○"}
              </span>
              <span className={s.available ? "text-ink" : "text-ink-3"}>
                {s.label}
                {s.note && <span className="ml-2 text-[11.5px] text-ink-3">{s.note}</span>}
                <span className="sr-only">{s.available ? " (measured)" : " (not available)"}</span>
              </span>
              <span className="tnum font-mono text-[10.5px] text-ink-2">
                {s.available
                  ? [s.value !== null && s.value !== undefined ? fmtNum(s.value, 1) : null, s.percentile !== null && s.percentile !== undefined ? `p${fmtNum(s.percentile, 0)}` : null]
                      .filter(Boolean)
                      .join(" · ") || "measured"
                  : "not available"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export interface AttentionGapMiniProps {
  gap: AttentionGap;
  className?: string;
}

/** Compact two-bar indicator for cards. */
export function AttentionGapMini({ gap, className }: AttentionGapMiniProps) {
  return (
    <div
      className={clsx("inline-flex items-center gap-2.5", className)}
      title={`Technical p${fmtNum(gap.technical, 0)} vs attention p${fmtNum(gap.attention, 0)} (${gap.coverage} coverage)`}
    >
      <div className="flex w-14 flex-col gap-[3px]" aria-hidden>
        <div className="h-[3px] bg-rule">
          <div className="h-full bg-ink" style={{ width: `${Math.max(0, Math.min(100, gap.technical))}%` }} />
        </div>
        <div className="h-[3px] bg-rule">
          <div className="h-full bg-ink-3" style={{ width: `${Math.max(0, Math.min(100, gap.attention))}%` }} />
        </div>
      </div>
      <span className={clsx("tnum font-mono text-[10px] uppercase tracking-[0.08em]", gap.flagged ? "text-accent" : "text-ink-2")}>
        {gap.flagged && "◆ "}Gap {fmtSigned(gap.gap, 0)}
        {gap.coverage === "low" && <span className="text-ink-3"> · low cov.</span>}
      </span>
    </div>
  );
}

function ordinal(v: number): string {
  const n = Math.round(v);
  const s = ["th", "st", "nd", "rd"];
  const m = n % 100;
  return `${n}${s[(m - 20) % 10] || s[m] || s[0]}`;
}
