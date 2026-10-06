import clsx from "clsx";
import { fmtDate } from "@/lib/format";
import type { EvidenceItem } from "@/lib/types";
import { SourceGlyph, SourceLink } from "./SourceLink";

export interface EvidenceListProps {
  items: EvidenceItem[];
  /** Cap the number of rows shown. */
  limit?: number;
  dense?: boolean;
  className?: string;
}

/** Evidence bullets: source glyph · title · detail · date · link. */
export function EvidenceList({ items, limit, dense, className }: EvidenceListProps) {
  const shown = limit ? items.slice(0, limit) : items;
  if (shown.length === 0) return <p className="text-[13px] text-ink-3">No evidence recorded.</p>;
  return (
    <ul className={clsx(dense ? "space-y-1.5" : "space-y-2.5", className)}>
      {shown.map((e, i) => (
        <li key={e.signal_id ?? `${e.title}-${i}`} className="grid grid-cols-[26px_1fr] gap-2.5">
          <SourceGlyph sourceType={e.source_type} className="mt-[2px]" />
          <div className="min-w-0">
            <p className={clsx("text-ink", dense ? "text-[13px] leading-snug" : "text-[13.5px] leading-snug")}>{e.title}</p>
            {e.detail && !dense && <p className="mt-0.5 text-[12.5px] leading-snug text-ink-2">{e.detail}</p>}
            <p className="mt-0.5 flex flex-wrap items-baseline gap-x-3">
              {e.event_at && <span className="tnum font-mono text-[10.5px] text-ink-3">{fmtDate(e.event_at)}</span>}
              <SourceLink url={e.source_url} />
            </p>
          </div>
        </li>
      ))}
      {limit && items.length > limit && (
        <li className="label pl-[36px] text-ink-3">+ {items.length - limit} more</li>
      )}
    </ul>
  );
}
