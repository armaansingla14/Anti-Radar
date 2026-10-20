"use client";

/** Print the stored brief (print.css hides navigation). */
export function PrintButton() {
  return (
    <button
      type="button"
      onClick={() => window.print()}
      className="inline-flex items-center gap-2 border border-ink bg-ink px-3.5 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-paper transition-colors duration-150 hover:border-accent hover:bg-accent print:hidden"
    >
      Print
    </button>
  );
}
