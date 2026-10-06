import clsx from "clsx";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { EvidenceList } from "@/components/EvidenceList";
import { fmtDate, humanize, pad2 } from "@/lib/format";
import type { StrangeSignal } from "@/lib/types";
import { EntityLinks } from "./EntityLinks";
import { clockTime } from "./prose";

const PATTERNS: Record<string, string> = {
  quiet_velocity: "Quiet velocity",
  shared_prior_employer_new_org: "Shared prior employer",
  lab_cluster: "Lab cluster",
  affiliation_exodus: "Affiliation exodus",
};

export function patternLabel(pattern: string): string {
  return PATTERNS[pattern] ?? humanize(pattern);
}

export interface StrangeSignalDispatchProps {
  signal: StrangeSignal;
  index: number;
  /** "rail" = compact sidebar dispatch; "full" = section-width with larger headline. */
  variant?: "rail" | "full";
  className?: string;
}

/**
 * A strange signal rendered as an intelligence dispatch: numbered, pattern
 * label, a serif headline, the deterministic detail, linked entities and the
 * underlying evidence behind a disclosure.
 */
export function StrangeSignalDispatch({ signal, index, variant = "rail", className }: StrangeSignalDispatchProps) {
  const full = variant === "full";
  return (
    <article className={clsx("relative", className)} aria-labelledby={`dispatch-${signal.id}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span aria-hidden className="inline-block size-[7px] bg-accent" />
        <span className="label text-accent">
          Dispatch {pad2(index)} · {patternLabel(signal.pattern)}
        </span>
        {signal.is_demo && <DemoBadge className="ml-auto" />}
      </div>
      <h3
        id={`dispatch-${signal.id}`}
        className={clsx(
          "mt-2 font-serif text-ink",
          full ? "text-[21px] leading-[1.22] tracking-[-0.005em]" : "text-[16.5px] leading-[1.28]",
        )}
      >
        {signal.headline}
      </h3>
      <p className={clsx("mt-1.5 text-ink-2", full ? "max-w-[68ch] text-[14px] leading-relaxed" : "text-[12.5px] leading-snug")}>
        {signal.detail}
      </p>
      <EntityLinks entities={signal.entities} limit={full ? 8 : 4} className="mt-2.5" />
      <details className="group mt-2.5">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3 marker:hidden hover:text-ink [&::-webkit-details-marker]:hidden">
          <span aria-hidden className="inline-block transition-transform duration-150 group-open:rotate-90">▸</span>
          <span>
            {signal.evidence.length} evidence {signal.evidence.length === 1 ? "signal" : "signals"}
          </span>
          <span aria-hidden>·</span>
          <span title="Pattern strength, 0–1">strength {signal.strength.toFixed(2)}</span>
          <span aria-hidden>·</span>
          <span title={`Detected ${fmtDate(signal.detected_at)} ${clockTime(signal.detected_at)}`}>
            {fmtDate(signal.detected_at, { year: false })}
          </span>
          <EpistemicBadge value="INFERENCE" compact className="ml-0.5" />
        </summary>
        <div className="mt-3 animate-fade-in border-l border-rule pl-3">
          <EvidenceList items={signal.evidence} dense limit={6} />
        </div>
      </details>
    </article>
  );
}
