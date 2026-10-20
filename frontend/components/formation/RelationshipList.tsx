import clsx from "clsx";
import Link from "next/link";
import { VerificationBadge } from "@/components/Badges";
import { SourceLink } from "@/components/SourceLink";
import { hrefFor } from "@/lib/format";
import type { EntityRef, RelationshipOut } from "@/lib/types";
import { relationshipSource, relationshipVerb, relationshipYears, sortRelationships } from "./logic";

function Ref({ r }: { r: EntityRef }) {
  const linkable = (r.type === "person" || r.type === "company") && (r.slug || r.id);
  return linkable ? (
    <Link href={hrefFor(r.type, r.slug || r.id)} className="link text-ink">
      {r.name}
    </Link>
  ) : (
    <span className="text-ink">{r.name}</span>
  );
}

/** "A — co-authored with — B · 2025 · arXiv ↗" with VERIFIED / INFERRED provenance. */
export function RelationshipList({ relationships, demo = false, limit = 10 }: { relationships: RelationshipOut[]; demo?: boolean; limit?: number }) {
  const sorted = sortRelationships(relationships);
  const shown = sorted.slice(0, limit);
  const rest = sorted.slice(limit);
  const row = (r: RelationshipOut) => {
    const years = relationshipYears(r);
    const src = relationshipSource(r);
    const inferred = r.epistemic !== "VERIFIED";
    const ev = r.evidence[0];
    return (
      <li key={r.id} className="grid grid-cols-[1fr_auto] items-baseline gap-x-4 py-2.5">
        <div className="min-w-0">
          <p className="text-[14px] leading-snug">
            <Ref r={r.src} />
            <span className={clsx("mx-2 font-mono text-[10.5px] uppercase tracking-[0.06em]", inferred ? "text-ink-3" : "text-ink-2")}>
              — {relationshipVerb(r.kind)} —
            </span>
            <Ref r={r.dst} />
          </p>
          {ev && (ev.title || ev.detail) && (
            <p className="mt-0.5 line-clamp-1 text-[12px] leading-snug text-ink-3" title={ev.detail ?? ev.title}>
              {ev.detail ?? ev.title}
            </p>
          )}
        </div>
        <div className="flex items-baseline gap-3 whitespace-nowrap">
          {years && <span className="tnum font-mono text-[10.5px] text-ink-2">{years}</span>}
          {src && (src.url ? <SourceLink url={src.url} demo={demo} className="font-mono text-[10.5px] text-ink-2">{src.label}</SourceLink> : <span className="font-mono text-[10.5px] text-ink-3">{src.label}</span>)}
          <VerificationBadge value={inferred ? "INFERRED" : "VERIFIED"} />
        </div>
      </li>
    );
  };
  return (
    <div>
      <ol className="divide-y divide-rule border-y border-rule">{shown.map(row)}</ol>
      {rest.length > 0 && (
        <details className="group">
          <summary className="label cursor-pointer list-none py-2.5 text-ink-2 hover:text-ink">
            <span aria-hidden className="mr-1 inline-block transition-transform group-open:rotate-90">→</span>
            {rest.length} more {rest.length === 1 ? "relationship" : "relationships"}
          </summary>
          <ol className="divide-y divide-rule border-b border-rule">{rest.map(row)}</ol>
        </details>
      )}
    </div>
  );
}
