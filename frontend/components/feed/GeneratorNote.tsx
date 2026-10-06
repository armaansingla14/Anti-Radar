import clsx from "clsx";
import type { Generator } from "@/lib/types";

const TEXT: Record<Generator, { label: string; note: string }> = {
  llm: { label: "LLM", note: "written by a language model from cited signals" },
  cached: { label: "LLM · cached", note: "language-model text cached from an earlier run" },
  template: { label: "Template", note: "LLM disabled — deterministic template output" },
};

export interface GeneratorNoteProps {
  generator: Generator | string;
  model?: string | null;
  /** Show the explanatory note after the label. */
  verbose?: boolean;
  className?: string;
}

/** Honest provenance for generated prose: who (or what) wrote this paragraph. */
export function GeneratorNote({ generator, model, verbose = true, className }: GeneratorNoteProps) {
  const meta = TEXT[generator as Generator] ?? { label: generator, note: "" };
  return (
    <span className={clsx("inline-flex flex-wrap items-baseline gap-x-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3", className)}>
      <span className="border border-rule-strong px-1 leading-[14px] text-ink-2">Generator · {meta.label}</span>
      {model && <span>{model}</span>}
      {verbose && meta.note && <span className="normal-case tracking-normal">{meta.note}</span>}
    </span>
  );
}
