import clsx from "clsx";
import { domainOf, fmtDate, isSafeUrl, sourceLabel } from "@/lib/format";
import type { SourceRef } from "@/lib/types";

export interface SourceListProps {
  sources: SourceRef[];
  className?: string;
}

/** Numbered bibliography: [S1] DOMAIN — Title (fetched date). */
export function SourceList({ sources, className }: SourceListProps) {
  if (sources.length === 0) {
    return <p className={clsx("text-[13px] text-ink-3", className)}>No sources recorded.</p>;
  }
  return (
    <ol className={clsx("divide-y divide-rule border-y border-rule", className)}>
      {sources.map((s, i) => {
        const id = /^S\d+$/.test(s.id) ? s.id : `S${i + 1}`;
        const domain = domainOf(s.url) ?? sourceLabel(s.source_type);
        return (
          <li key={`${s.id}-${i}`} id={`source-${id}`} className="grid grid-cols-[44px_1fr_auto] items-baseline gap-3 py-2">
            <span className="tnum font-mono text-[11px] text-accent">[{id}]</span>
            <span className="min-w-0">
              <span className="label mr-2 text-ink-3">{domain}</span>
              {isSafeUrl(s.url) ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="link text-[13px] text-ink">
                  {s.title || s.url}
                </a>
              ) : (
                <span className="text-[13px] text-ink">{s.title || "Untitled source"}</span>
              )}
            </span>
            <span className="tnum font-mono text-[10.5px] text-ink-3">{s.fetched_at ? fmtDate(s.fetched_at) : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}
