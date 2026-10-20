"use client";

import clsx from "clsx";
import { useState } from "react";
import { fmtDate } from "@/lib/format";
import { countSources, groupByFamily, sourcesLabel } from "@/lib/labels";
import type { EvidenceItem } from "@/lib/types";
import { Sheet } from "./Sheet";
import { SourceGlyph, SourceLink } from "./SourceLink";

export interface SourceDrawerProps {
  open: boolean;
  onClose: () => void;
  /** The claim the sources support, e.g. a headline or "Why DeepLethe surfaced". */
  claim: string;
  evidence: EvidenceItem[];
  /** Fictional entity: never link to real hosts. */
  demo?: boolean;
}

/** Every source behind a claim, grouped by independent family, dated, with outbound links. */
export function SourceDrawer({ open, onClose, claim, evidence, demo = false }: SourceDrawerProps) {
  const groups = groupByFamily(evidence);
  const n = countSources(evidence);
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={`${sourcesLabel(n)} — ${claim}`}
      width={540}
      header={
        <>
          <p className="label mb-1.5">
            {sourcesLabel(n)} · {groups.length} {groups.length === 1 ? "family" : "independent families"}
          </p>
          <h2 className="max-w-[44ch] font-serif text-[22px] leading-[1.2] text-ink">{claim}</h2>
        </>
      }
    >
      {groups.length === 0 ? (
        <p className="text-[13.5px] text-ink-2">No sources are recorded for this claim.</p>
      ) : (
        <div className="space-y-7">
          {groups.map((g) => (
            <section key={g.key} aria-label={g.label}>
              <div className="flex items-center gap-3">
                <h3 className="label shrink-0 text-ink">{g.label}</h3>
                <span aria-hidden className="h-px flex-1 bg-rule" />
                <span className="label tnum shrink-0">{g.items.length}</span>
              </div>
              <ul className="mt-2 divide-y divide-rule">
                {g.items.map((e, i) => (
                  <li key={`${e.signal_id ?? e.title}-${i}`} className="grid grid-cols-[26px_1fr_auto] items-baseline gap-x-2.5 py-2">
                    <SourceGlyph sourceType={e.source_type} className="translate-y-[2px]" />
                    <div className="min-w-0">
                      <p className="text-[13px] leading-snug text-ink">{e.title}</p>
                      {e.detail && <p className="mt-0.5 text-[12px] leading-snug text-ink-2">{e.detail}</p>}
                      <SourceLink url={e.source_url} demo={demo} className="mt-0.5 inline-block" />
                      {!e.source_url && <span className="font-mono text-[10px] text-ink-3">Derived from stored history · no link</span>}
                    </div>
                    <span className="tnum whitespace-nowrap font-mono text-[10.5px] text-ink-3">{e.event_at ? fmtDate(e.event_at) : "undated"}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <p className="border-t border-rule pt-3 font-mono text-[10px] leading-relaxed text-ink-3">
            Families count as independent only when they come from different systems (code, papers, job boards, the web,
            community, social). Repeated readings of one source count once.
          </p>
        </div>
      )}
    </Sheet>
  );
}

export interface SourcesButtonProps {
  claim: string;
  evidence: EvidenceItem[];
  /** Override the displayed count (e.g. backend `sources_count`). */
  count?: number;
  demo?: boolean;
  className?: string;
}

/** "3 SOURCES" — opens the SourceDrawer. Renders a muted "No sources" when empty. */
export function SourcesButton({ claim, evidence, count, demo, className }: SourcesButtonProps) {
  const [open, setOpen] = useState(false);
  const n = count ?? countSources(evidence);
  const base = "font-mono text-[10.5px] uppercase tracking-[0.08em] whitespace-nowrap";
  if (n === 0 || evidence.length === 0) {
    return <span className={clsx(base, "text-ink-3", className)}>{n > 0 ? sourcesLabel(n) : "No sources"}</span>;
  }
  return (
    <>
      <button
        type="button"
        data-action="sources"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
        className={clsx(base, "text-ink-2 underline decoration-rule-strong underline-offset-[3px] transition-colors hover:text-ink hover:decoration-accent", className)}
      >
        <span className="tnum">{sourcesLabel(n)}</span>
      </button>
      <SourceDrawer open={open} onClose={() => setOpen(false)} claim={claim} evidence={evidence} demo={demo} />
    </>
  );
}
