import clsx from "clsx";
import Link from "next/link";
import {
  ACTION_DEFINITION,
  MATERIALITY_DEFINITION,
  actionKey,
  actionLabel,
  compTypeLabel,
  materialityLabel,
  methodologyHref,
  portfolioHref,
  priorityLabel,
  questionStatusLabel,
  relationLabel,
} from "./logic";

const chip =
  "inline-flex items-center gap-1 rounded-xs px-1.5 py-[1px] font-mono text-[9.5px] uppercase leading-[14px] tracking-[0.08em] whitespace-nowrap";

const MAT_CLS: Record<string, string> = {
  MATERIAL: "border border-accent bg-accent text-paper",
  WATCH: "border border-ink text-ink",
  NOISE: "border border-dotted border-ink-3 text-ink-3",
};

/** MATERIAL (filled) / WATCH (outlined) / NOISE (dotted). The title says what the class means. */
export function MaterialityBadge({ value, className }: { value: string; className?: string }) {
  const v = materialityLabel(value);
  return (
    <span className={clsx(chip, MAT_CLS[v] ?? MAT_CLS.NOISE, className)} title={MATERIALITY_DEFINITION[v]} data-materiality={v}>
      {v}
    </span>
  );
}

/** Rule id that decided the materiality, linking to the methodology table. */
export function RuleLink({ rule, className }: { rule: string; className?: string }) {
  return (
    <a
      href={methodologyHref("materiality")}
      target="_blank"
      rel="noopener noreferrer"
      className={clsx("font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2 underline decoration-rule-strong underline-offset-2 hover:text-accent", className)}
      title={`Materiality rule ${rule} — docs/PORTFOLIO_METHODOLOGY.md`}
    >
      Rule {rule}
    </a>
  );
}

const PRI_CLS: Record<string, string> = {
  HIGH: "border border-accent bg-accent text-paper",
  MEDIUM: "border border-accent text-accent",
  LOW: "border border-rule-strong text-ink-2",
};

export function PriorityBadge({ value, className }: { value: string | null; className?: string }) {
  const v = (value ?? "").toUpperCase();
  return (
    <span className={clsx(chip, PRI_CLS[v] ?? "border border-dotted border-ink-3 text-ink-3", className)} title="Needs Attention priority (documented scoring)">
      {priorityLabel(v)}
    </span>
  );
}

/** One of the six review actions — never "contact founder". */
export function ActionChip({ value, className }: { value: string; className?: string }) {
  return (
    <span className={clsx(chip, "border border-ink bg-ink text-paper", className)} title={ACTION_DEFINITION[actionKey(value)]}>
      <span aria-hidden>→</span>
      {actionLabel(value)}
    </span>
  );
}

export function RelationChip({ value, className, title }: { value: string; className?: string; title?: string }) {
  const v = relationLabel(value);
  return (
    <span className={clsx(chip, v === "SELF" ? "border border-rule-strong text-ink-2" : "border border-dashed border-ink-2 text-ink", className)} title={title}>
      {v}
    </span>
  );
}

export function CompTypeChip({ value, className }: { value: string; className?: string }) {
  const v = compTypeLabel(value);
  return <span className={clsx(chip, v === "DIRECT" ? "border border-ink text-ink" : "border border-rule-strong text-ink-2", className)}>{v}</span>;
}

export function QuestionStatusChip({ value, className }: { value: string; className?: string }) {
  const v = questionStatusLabel(value);
  return <span className={clsx(chip, v === "OPEN" ? "border border-ink text-ink" : "border border-dotted border-ink-3 text-ink-3", className)}>{v}</span>;
}

/** Small mark on company rows: a publicly disclosed Anti Fund portfolio company. */
export function PortfolioBadge({ slug, className, link = true }: { slug: string; className?: string; link?: boolean }) {
  const cls = clsx(chip, "border border-ink text-ink", link && "hover:bg-ink hover:text-paper", className);
  const title = "Publicly disclosed Anti Fund portfolio company (antifund.com) — open its Portfolio Pulse view";
  return link ? (
    <Link href={portfolioHref(slug)} className={cls} title={title}>
      Portfolio
    </Link>
  ) : (
    <span className={cls} title={title}>
      Portfolio
    </span>
  );
}

/** "◆ INFERENCE" — a judgement derived from facts (comps, "what this could mean", why it matters). */
export function InferenceTag({ className }: { className?: string }) {
  return (
    <span className={clsx(chip, "border border-rule-strong text-ink-2", className)} title="Inference: a judgement derived from the cited facts, not a fact itself">
      <span aria-hidden className="text-[8px]">◆</span>
      Inference
    </span>
  );
}

/** "● FACT" — observed in a cited source. */
export function FactTag({ className }: { className?: string }) {
  return (
    <span className={clsx(chip, "border border-ink/70 text-ink", className)} title="Fact: observed in a cited source">
      <span aria-hidden className="text-[8px]">●</span>
      Fact
    </span>
  );
}

export function EpistemicTag({ value, className }: { value: string; className?: string }) {
  return (value ?? "").toUpperCase() === "FACT" ? <FactTag className={className} /> : <InferenceTag className={className} />;
}
