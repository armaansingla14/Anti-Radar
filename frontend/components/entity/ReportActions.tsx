"use client";

import { reportMarkdownUrl } from "@/lib/api";

const btn =
  "inline-flex items-center gap-1.5 border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper";

/** Export markdown (download via proxy) and print. Hidden when printing. */
export function ReportActions({ id, filename }: { id: string; filename: string }) {
  return (
    <div className="flex flex-wrap items-center gap-3 print:hidden">
      <a href={reportMarkdownUrl(id)} download={filename} className={btn}>
        <span aria-hidden>↓</span> Export Markdown
      </a>
      <button type="button" onClick={() => window.print()} className={btn}>
        <span aria-hidden>⎙</span> Print
      </button>
    </div>
  );
}
