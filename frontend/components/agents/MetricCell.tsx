import clsx from "clsx";
import type { MetricValue } from "@/lib/types-agents";
import { formatMetric, type MetricKind } from "./logic";

/** One metric value: the number with its n in a tooltip, or "—" with a visible footnote mark and the reason. */
export function MetricCell({ m, kind, notes, className }: { m: MetricValue | undefined; kind: MetricKind; notes: Map<string, number>; className?: string }) {
  const f = formatMetric(m, kind);
  const mark = f.missing && f.reason ? notes.get(f.reason) : undefined;
  return (
    <span className={clsx("tnum whitespace-nowrap font-mono text-[11.5px]", f.missing ? "text-ink-3" : "text-ink", className)} title={f.title}>
      {f.text}
      {mark !== undefined && (
        <sup className="ml-[1px] text-[8.5px] text-accent">
          <a href={`#note-${mark}`} className="no-underline">
            {mark}
          </a>
        </sup>
      )}
    </span>
  );
}

/** Footnotes for null metrics, numbered as in the table. */
export function MetricNotes({ notes }: { notes: Map<string, number> }) {
  if (notes.size === 0) return null;
  return (
    <ol className="mt-3 space-y-0.5">
      {[...notes.entries()].map(([reason, n]) => (
        <li key={n} id={`note-${n}`} className="grid scroll-mt-24 grid-cols-[18px_minmax(0,1fr)] text-[11.5px] leading-snug text-ink-2 target:bg-accent-wash">
          <span className="tnum font-mono text-[10px] text-accent">{n}</span>
          <span>
            <span className="font-mono text-[10.5px] text-ink-3">— </span>
            Not computed: {reason}
          </span>
        </li>
      ))}
    </ol>
  );
}
