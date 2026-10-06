import Link from "next/link";
import { SourceGlyph } from "@/components/SourceLink";
import { domainOf, externalHref, fmtDate, sourceLabel } from "@/lib/format";
import type { JsonObject, SourceRef } from "@/lib/types";

/** radar://company/<slug> → /companies/<slug>; other internal refs → null. */
export function internalHref(url: string | null | undefined): string | null {
  if (!url?.startsWith("radar://")) return null;
  const m = /^radar:\/\/(company|person|founder)\/([^#?/]+)/.exec(url);
  if (!m) return null;
  return m[1] === "company" ? `/companies/${m[2]}` : `/founders/${m[2]}`;
}

/** Coerce a report/entity source record into a SourceRef. */
export function asSourceRef(s: JsonObject | SourceRef, i: number): SourceRef {
  const o = s as JsonObject;
  return {
    id: typeof o.id === "string" ? o.id : `S${i + 1}`,
    url: typeof o.url === "string" ? o.url : null,
    title: typeof o.title === "string" ? o.title : null,
    source_type: typeof o.source_type === "string" ? o.source_type : null,
    fetched_at: typeof o.fetched_at === "string" ? o.fetched_at : null,
  };
}

/**
 * Numbered bibliography with anchors (#source-S3) that inline citations link
 * to. Internal radar:// references resolve to in-app pages; non-http URLs
 * never become links.
 */
export function Sources({
  sources,
  idPrefix = "source",
  demo = false,
}: {
  sources: (SourceRef | JsonObject)[];
  idPrefix?: string;
  /** Sources for a fictional entity: never link to real hosts. */
  demo?: boolean;
}) {
  const refs = sources.map(asSourceRef);
  if (refs.length === 0) return <p className="border-y border-rule py-4 text-[13px] text-ink-3">No sources recorded.</p>;
  return (
    <ol className="divide-y divide-rule border-y border-rule">
      {refs.map((s, i) => {
        const id = /^S\d+$/.test(s.id) ? s.id : `S${i + 1}`;
        const internal = internalHref(s.url);
        const domain = internal ? "anti radar" : (domainOf(s.url) ?? sourceLabel(s.source_type));
        const title = s.title || s.url || "Untitled source";
        const href = internal ? null : externalHref(s.url, demo);
        return (
          <li
            key={`${s.id}-${i}`}
            id={`${idPrefix}-${id}`}
            className="grid scroll-mt-24 grid-cols-[46px_28px_1fr_auto] items-baseline gap-2 py-2 target:bg-accent-wash"
          >
            <span className="tnum font-mono text-[11px] text-accent">[{id}]</span>
            <SourceGlyph sourceType={s.source_type} />
            <span className="min-w-0 break-words">
              {internal ? (
                <Link href={internal} className="link text-[13px] text-ink">
                  {title}
                </Link>
              ) : href ? (
                <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="link text-[13px] text-ink">
                  {title}
                  <span aria-hidden className="ml-1 text-ink-3">↗</span>
                </a>
              ) : (
                <span className="text-[13px] text-ink">{title}</span>
              )}
              <span className="label ml-2 text-ink-3">{domain}</span>
            </span>
            <span className="tnum whitespace-nowrap font-mono text-[10.5px] text-ink-3">{s.fetched_at ? fmtDate(s.fetched_at) : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}
