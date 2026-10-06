import clsx from "clsx";
import { EpistemicBadge } from "@/components/Badges";
import { SourceGlyph, SourceLink } from "@/components/SourceLink";
import { fmtDate } from "@/lib/format";
import type { AntiScore, EvidenceItem, Narrative } from "@/lib/types";
import { Cite, GeneratorLabel } from "./primitives";

/** signal_id → evidence item, gathered from every score component and the collision. */
export function evidenceIndex(score: AntiScore | null | undefined): Map<string, EvidenceItem> {
  const map = new Map<string, EvidenceItem>();
  if (!score) return map;
  const all = [...score.components.flatMap((c) => c.evidence), ...(score.collision?.evidence ?? [])];
  for (const e of all) if (e.signal_id && !map.has(e.signal_id)) map.set(e.signal_id, e);
  return map;
}

export interface NarrativeBlockProps {
  narrative: Narrative;
  /** Resolve signal-id citations to titled evidence. */
  evidence?: Map<string, EvidenceItem>;
  size?: "lead" | "body";
  className?: string;
}

/**
 * Narrative paragraph with its provenance: generator (LLM model / template),
 * epistemic status and numbered citations. Signal-id citations resolve to
 * footnotes; "S#" citations link to the page source list.
 */
export function NarrativeBlock({ narrative, evidence, size = "body", className }: NarrativeBlockProps) {
  const sIds = narrative.citations.filter((c) => /^S\d+$/.test(c));
  const notes = narrative.citations
    .filter((c) => !/^S\d+$/.test(c))
    .map((c) => ({ id: c, ev: evidence?.get(c) ?? null }));
  return (
    <div className={clsx("min-w-0", className)}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <EpistemicBadge value={narrative.epistemic} />
        <GeneratorLabel generator={narrative.generator} model={narrative.model} />
      </div>
      <p
        className={clsx(
          "max-w-[68ch] text-ink",
          size === "lead" ? "font-serif text-[21px] leading-[1.45] tracking-[-0.005em]" : "text-[15px] leading-[1.65]",
        )}
      >
        {narrative.text}
        {notes.map((n, i) => (
          <sup key={n.id} className="ml-[2px] font-mono text-[0.55em] text-accent">
            <a href={`#note-${n.id}`} className="no-underline hover:underline">
              {i + 1}
            </a>
          </sup>
        ))}
        {sIds.map((c) => (
          <Cite key={c} id={c} />
        ))}
      </p>
      {notes.length > 0 && (
        <ol className="mt-4 space-y-1.5 border-t border-rule pt-3">
          {notes.map((n, i) => (
            <li key={n.id} id={`note-${n.id}`} className="grid scroll-mt-24 grid-cols-[18px_26px_1fr] items-baseline gap-1.5 text-[12.5px] leading-snug">
              <span className="tnum font-mono text-[10.5px] text-accent">{i + 1}</span>
              {n.ev ? <SourceGlyph sourceType={n.ev.source_type} /> : <span />}
              <span className="min-w-0 text-ink-2">
                {n.ev ? (
                  <>
                    <span className="text-ink">{n.ev.title}</span>
                    {n.ev.event_at && <span className="tnum ml-2 font-mono text-[10px] text-ink-3">{fmtDate(n.ev.event_at)}</span>}
                    <SourceLink url={n.ev.source_url} className="ml-2" />
                  </>
                ) : (
                  <span className="font-mono text-[10.5px] text-ink-3" title={n.id}>
                    Signal {n.id.slice(0, 8)} — not in the current score evidence
                  </span>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
