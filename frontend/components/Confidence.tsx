import clsx from "clsx";
import { CONFIDENCE_TITLE, confidenceLevel } from "@/lib/labels";

export interface ConfidenceProps {
  /** HIGH CONFIDENCE / MEDIUM CONFIDENCE / EARLY SIGNAL (case/abbreviation tolerant). Renders nothing when absent. */
  value: string | null | undefined;
  className?: string;
}

const STYLE = {
  "HIGH CONFIDENCE": { mark: "bg-ink", text: "text-ink" },
  "MEDIUM CONFIDENCE": { mark: "bg-ink-2", text: "text-ink-2" },
  "EARLY SIGNAL": { mark: "border border-ink-3 bg-transparent", text: "text-ink-3" },
} as const;

/**
 * Evidence-breadth confidence. Restrained: a three-step mark and mono caps,
 * no colour — the accent is reserved for the primary number on a surface.
 */
export function Confidence({ value, className }: ConfidenceProps) {
  const level = confidenceLevel(value);
  if (!level) return null;
  const s = STYLE[level];
  const filled = level === "HIGH CONFIDENCE" ? 3 : level === "MEDIUM CONFIDENCE" ? 2 : 1;
  return (
    <span
      className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap font-mono text-[9.5px] uppercase leading-[14px] tracking-[0.08em]", s.text, className)}
      title={CONFIDENCE_TITLE[level]}
    >
      <span aria-hidden className="inline-flex items-end gap-[2px]">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={clsx("inline-block w-[3px]", i < filled ? s.mark : "bg-rule")}
            style={{ height: 4 + i * 2.5 }}
          />
        ))}
      </span>
      {level}
    </span>
  );
}
