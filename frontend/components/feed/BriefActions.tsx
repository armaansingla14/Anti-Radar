"use client";

const btn =
  "inline-flex items-center gap-2 border px-3.5 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] transition-colors duration-150";

/** Export / print controls for the morning brief. Hidden when printing. */
export function BriefActions({ date }: { date: string }) {
  return (
    <div className="flex flex-wrap items-center gap-3 print:hidden">
      <a
        href="/api/proxy/brief/markdown"
        download={`anti-radar-brief-${date}.md`}
        className={`${btn} border-rule-strong text-ink hover:border-ink`}
      >
        <span aria-hidden>↓</span> Export Markdown
      </a>
      <button type="button" onClick={() => window.print()} className={`${btn} border-ink bg-ink text-paper hover:bg-accent hover:border-accent`}>
        Print
      </button>
    </div>
  );
}
