import clsx from "clsx";
import { ratingText, ratingTone, scaleLegend, stateLabel, statusLabel, statusTone, testabilityLabel, type RatingTone, type StatusTone } from "./logic";

const chip =
  "inline-flex items-center gap-1 rounded-xs px-1.5 py-[1px] font-mono text-[9.5px] uppercase leading-[14px] tracking-[0.08em] whitespace-nowrap";

const STATUS_CLS: Record<StatusTone, string> = {
  verified: "border border-ink bg-ink text-paper",
  supported: "border border-ink text-ink",
  partial: "border border-dashed border-ink-2 text-ink",
  conflict: "border border-accent bg-accent text-paper",
  unverified: "border border-dotted border-ink-3 text-ink-2",
  untestable: "hatch text-ink-2",
};

const STATUS_GLYPH: Record<StatusTone, string> = {
  verified: "■",
  supported: "▣",
  partial: "◧",
  conflict: "◆",
  unverified: "□",
  untestable: "⊘",
};

/** Claim verification status. CONTRADICTED renders as EVIDENCE CONFLICT. */
export function ClaimStatusBadge({
  status,
  labels,
  definition,
  className,
}: {
  status: string;
  labels?: Record<string, string>;
  definition?: string | null;
  className?: string;
}) {
  const tone = statusTone(status);
  const label = statusLabel(status, labels);
  return (
    <span className={clsx(chip, STATUS_CLS[tone], className)} title={definition ?? label} data-status={status}>
      <span aria-hidden className="text-[8px]">{STATUS_GLYPH[tone]}</span>
      {label}
    </span>
  );
}

const STATE_CLS: Record<string, string> = {
  NOT_RUN: "border border-dotted border-ink-3 text-ink-3",
  IN_PROGRESS: "border border-dashed border-ink-2 text-ink-2",
  COMPLETE: "border border-ink text-ink",
  NEEDS_REVIEW: "border border-accent text-accent",
};

/** Workspace state: NOT RUN / IN PROGRESS / COMPLETE / NEEDS REVIEW. */
export function DiligenceStateBadge({ state, title, large, className }: { state: string; title?: string | null; large?: boolean; className?: string }) {
  return (
    <span
      className={clsx(chip, STATE_CLS[state] ?? STATE_CLS.NOT_RUN, large && "px-2 py-[3px] text-[10.5px] leading-[16px]", className)}
      title={title ?? undefined}
      data-diligence-state={state}
    >
      {state === "NEEDS_REVIEW" && <span aria-hidden>●</span>}
      {stateLabel(state)}
    </span>
  );
}

const RATING_CLS: Record<RatingTone, string> = {
  bad: "border border-accent text-accent",
  mid: "border border-ink text-ink",
  good: "border border-rule-strong text-ink-2",
  unknown: "border border-dotted border-ink-3 text-ink-3",
};

/** "Risk: HIGH" / "Evidence: MEDIUM" — the scale is always named. */
export function RatingBadge({ scale, rating, className }: { scale: string; rating: string; className?: string }) {
  return (
    <span className={clsx(chip, RATING_CLS[ratingTone({ scale, rating })], className)} title={scaleLegend(scale)}>
      {ratingText({ scale, rating })}
    </span>
  );
}

/** Testability chip with its definition as the tooltip. */
export function TestabilityChip({ value, definition, className }: { value: string; definition?: string | null; className?: string }) {
  return (
    <span className={clsx(chip, "border border-rule-strong text-ink-2", className)} title={definition ?? undefined}>
      Testability {testabilityLabel(value)}
    </span>
  );
}
