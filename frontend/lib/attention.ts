/**
 * Display logic for the attention gap. Public attention is a cohort percentile
 * when at least one attention source was measured, and null when none was —
 * an unmeasured value must never read as "0th percentile".
 */
import type { AttentionGap } from "./types";

export const ATTENTION_NOT_MEASURED = "not measured";

export function attentionMeasured(gap: Pick<AttentionGap, "attention">): gap is { attention: number } {
  return gap.attention !== null && gap.attention !== undefined && Number.isFinite(gap.attention);
}

export function ordinal(v: number): string {
  const n = Math.round(v);
  const s = ["th", "st", "nd", "rd"];
  const m = n % 100;
  return `${n}${s[(m - 20) % 10] || s[m] || s[0]}`;
}

/** "p42" or "not measured". */
export function attentionShort(gap: Pick<AttentionGap, "attention">): string {
  return attentionMeasured(gap) ? `p${Math.round(gap.attention)}` : ATTENTION_NOT_MEASURED;
}

/** One-sentence plain-English reading of the gap. */
export function attentionSentence(gap: Pick<AttentionGap, "technical" | "attention" | "gap">): string {
  if (!attentionMeasured(gap)) {
    return `Technical activity sits at the ${ordinal(gap.technical)} percentile of the cohort; public attention was not measured — no attention source is available — so the gap is not an observed difference.`;
  }
  return gap.gap > 0
    ? `Technical activity sits at the ${ordinal(gap.technical)} percentile of the cohort; public attention at the ${ordinal(gap.attention)}.`
    : `Public attention is at or ahead of technical activity relative to the cohort.`;
}

/** Tooltip for the compact indicator. */
export function attentionTitle(gap: Pick<AttentionGap, "technical" | "attention" | "coverage">): string {
  return `Technical p${Math.round(gap.technical)} vs attention ${attentionShort(gap)} (${gap.coverage} coverage)`;
}
