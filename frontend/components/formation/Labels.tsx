import clsx from "clsx";
import { VerificationBadge } from "@/components/Badges";

/** EMERGING TEAM / POSSIBLE FORMATION / FORMATION SIGNALS DETECTED — never "founded". */
export function FormationLabel({ label, tier, className }: { label: string; tier?: string; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink", className)}>
      <span aria-hidden className={tier === "high" ? "text-accent" : tier === "emerging" ? "text-ink" : "text-ink-3"}>
        {tier === "early" ? "◇" : "◆"}
      </span>
      {label}
    </span>
  );
}

/** "Theme / subtopic" mono line. */
export function ThemeLine({ theme, subtopic, className }: { theme: string | null; subtopic: string | null; className?: string }) {
  if (!theme && !subtopic) return null;
  return (
    <span className={clsx("font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2", className)}>
      {theme}
      {theme && subtopic && <span className="mx-1.5 text-ink-3">/</span>}
      {subtopic && <span className="text-ink-3">{subtopic}</span>}
    </span>
  );
}

/** Profile-derived affiliation marker: INFERRED, self-reported. */
export function SelfReported({ className }: { className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1", className)} title="From GitHub profile company/bio or other self-description — not verified">
      <VerificationBadge value="INFERRED" />
      <span className="font-mono text-[9.5px] uppercase tracking-[0.08em] text-ink-3">self-reported</span>
    </span>
  );
}
