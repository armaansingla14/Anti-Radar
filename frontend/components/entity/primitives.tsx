import clsx from "clsx";
import type { ReactNode } from "react";
import { SectionLabel } from "@/components/SectionLabel";
import { humanize } from "@/lib/format";
import type { Generator } from "@/lib/types";

/** Numbered editorial section with an anchor id. */
export function Section({
  n,
  id,
  title,
  right,
  children,
  className,
}: {
  n?: number;
  id: string;
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={clsx("min-w-0 scroll-mt-24", className)}>
      <SectionLabel index={n} id={`${id}-title`} right={right}>
        {title}
      </SectionLabel>
      <div className="mt-5">{children}</div>
    </section>
  );
}

/** Honest one-line empty state used inside sections. */
export function Quiet({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={clsx("border-y border-rule py-4 text-[13px] leading-relaxed text-ink-3", className)}>{children}</p>;
}

/** Who wrote this text: LLM (with model) or the deterministic template. */
export function GeneratorLabel({
  generator,
  model,
  className,
}: {
  generator: Generator | string | null | undefined;
  model?: string | null;
  className?: string;
}) {
  const g = (generator ?? "template").toLowerCase();
  const text =
    g === "llm" ? `LLM · ${model ?? "model unknown"}` : g === "cached" ? `LLM (cached)${model ? ` · ${model}` : ""}` : "Template";
  const title =
    g === "template"
      ? "Deterministic template built from database facts — no language model was used (LLM disabled on this instance)"
      : g === "cached"
        ? "Language-model output served from cache"
        : "Written by a language model; verify claims against the cited sources";
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap border border-rule px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em] text-ink-2",
        className,
      )}
      title={title}
    >
      <span aria-hidden className="text-ink-3">
        {g === "template" ? "▤" : "◇"}
      </span>
      {text}
    </span>
  );
}

/** "[S3]" style citation superscript linking to the page's source list. */
export function Cite({ id, prefix = "source", className }: { id: string; prefix?: string; className?: string }) {
  const clean = id.replace(/^\[|\]$/g, "");
  return (
    <sup className={clsx("ml-[2px] font-mono text-[0.68em] leading-none", className)}>
      <a href={`#${prefix}-${clean}`} className="text-accent no-underline hover:underline" title={`Source ${clean}`}>
        {clean}
      </a>
    </sup>
  );
}

/** Small mono key/value pair. */
export function Kv({ k, children, className }: { k: string; children: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-baseline gap-2", className)}>
      <span className="label text-ink-3">{k}</span>
      <span className="tnum font-mono text-[11.5px] text-ink">{children}</span>
    </div>
  );
}

/** "pre_seed" → "Pre-seed"; "series_a" → "Series A". */
export function stageText(stage: string | null | undefined): string {
  if (!stage || stage === "unknown") return "Unknown";
  if (stage === "pre_seed") return "Pre-seed";
  if (stage.startsWith("series_")) return `Series ${stage.slice(7).toUpperCase()}`;
  return humanize(stage);
}

/** Table-of-contents strip under entity headers. */
export function Contents({ items }: { items: { id: string; title: string }[] }) {
  return (
    <nav aria-label="On this page" className="flex flex-wrap gap-x-4 gap-y-1.5 border-b border-rule py-3">
      {items.map((it, i) => (
        <a
          key={it.id}
          href={`#${it.id}`}
          className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-accent"
        >
          <span className="tnum mr-1 text-ink-3">{String(i + 1).padStart(2, "0")}</span>
          {it.title}
        </a>
      ))}
    </nav>
  );
}
