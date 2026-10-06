import clsx from "clsx";
import { EpistemicBadge, VerificationBadge } from "@/components/Badges";
import { Markdown } from "@/components/Markdown";
import { humanize } from "@/lib/format";
import type { Epistemic, JsonObject, JsonValue } from "@/lib/types";
import { Cite } from "./primitives";

/** One claim in a structured report body. */
export interface Claim {
  text: string;
  epistemic: Epistemic | null;
  sourceIds: string[];
}

const EPISTEMICS = new Set(["FACT", "INFERENCE", "MODEL_OPINION"]);

function toClaim(v: JsonValue): Claim | null {
  if (typeof v === "string") return { text: v, epistemic: null, sourceIds: [] };
  if (typeof v === "number") return { text: String(v), epistemic: null, sourceIds: [] };
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const o = v as JsonObject;
    const text = typeof o.text === "string" ? o.text : typeof o.claim === "string" ? o.claim : typeof o.question === "string" ? o.question : null;
    if (!text) return null;
    const ep = typeof o.epistemic === "string" && EPISTEMICS.has(o.epistemic.toUpperCase()) ? (o.epistemic.toUpperCase() as Epistemic) : null;
    const raw = Array.isArray(o.source_ids) ? o.source_ids : Array.isArray(o.sources) ? o.sources : Array.isArray(o.citations) ? o.citations : [];
    return { text, epistemic: ep, sourceIds: raw.filter((s: unknown): s is string => typeof s === "string") };
  }
  return null;
}

/** Normalise a section value into claims; null when the shape is unrecognised. */
export function toClaims(v: JsonValue): Claim[] | null {
  if (v === null || v === undefined) return [];
  if (Array.isArray(v)) {
    const out = v.map(toClaim);
    return out.every(Boolean) ? (out as Claim[]) : null;
  }
  const one = toClaim(v);
  return one ? [one] : null;
}

/** Strip inline "[S3]" markers from text when they are rendered separately. */
function splitInlineCites(text: string): { text: string; ids: string[] } {
  const ids: string[] = [];
  const clean = text.replace(/\s*\[(S\d+)\]/g, (_, id: string) => {
    ids.push(id);
    return "";
  });
  return { text: clean.trim(), ids };
}

export function ClaimLine({
  claim,
  numbered,
  index,
  citePrefix,
}: {
  claim: Claim;
  numbered?: boolean;
  index?: number;
  citePrefix?: string;
}) {
  const { text, ids } = splitInlineCites(claim.text);
  const cites = Array.from(new Set([...claim.sourceIds, ...ids]));
  return (
    <li className="grid grid-cols-[22px_1fr] gap-x-1 py-2 text-[14.5px] leading-[1.6] text-ink">
      <span aria-hidden className={clsx("tnum pt-[3px] font-mono text-[10.5px]", numbered ? "text-ink-2" : "text-ink-3")}>
        {numbered ? String((index ?? 0) + 1).padStart(2, "0") : "—"}
      </span>
      <span className="min-w-0">
        {text}
        {cites.map((c) => (
          <Cite key={c} id={c} prefix={citePrefix} />
        ))}
        {claim.epistemic && (
          <EpistemicBadge value={claim.epistemic} compact={claim.epistemic === "FACT"} className="ml-2 align-[2px]" />
        )}
      </span>
    </li>
  );
}

export interface ReportSectionDef {
  key: string;
  title: string;
  numbered?: boolean;
  /** Copy for an empty section. */
  empty?: string;
}

export const BRIEF_SECTIONS: ReportSectionDef[] = [
  { key: "what_they_do", title: "What they do" },
  { key: "why_now", title: "Why now" },
  { key: "what_changed", title: "What changed" },
  { key: "founders", title: "Founders" },
  { key: "technical_edge", title: "Technical edge" },
  { key: "market", title: "Market" },
  { key: "competitors", title: "Competitors" },
  { key: "why_this_could_matter", title: "Why this could matter" },
  { key: "why_this_could_be_wrong", title: "Why this could be wrong" },
  { key: "contradictory_signals", title: "Contradictory signals" },
  { key: "questions_to_ask_founders", title: "Questions to ask the founders", numbered: true },
  { key: "signals", title: "Signals" },
  { key: "unknowns", title: "Unknowns" },
];

export const MEMO_SECTIONS: ReportSectionDef[] = [
  { key: "anti_score_summary", title: "Anti Score" },
  { key: "why_on_radar", title: "Why it is on our radar" },
  { key: "why_now", title: "Why now" },
  { key: "founders", title: "Founders" },
  { key: "technical_insight", title: "Technical insight" },
  { key: "market", title: "Market" },
  { key: "competition", title: "Competition" },
  { key: "what_others_may_be_missing", title: "What others may be missing" },
  { key: "evidence", title: "Evidence" },
  { key: "risks", title: "Risks" },
  { key: "questions_to_answer", title: "Questions to answer", numbered: true },
];

/** Keys that are header metadata rather than sections. */
const META_KEYS = new Set(["date", "subject", "company", "person", "one_liner", "entity", "title", "generator", "model"]);

/** Ordered sections present in a body (known ones first, then any extras). */
export function sectionsFor(kind: string, body: JsonObject): ReportSectionDef[] {
  const base = kind === "memo" ? MEMO_SECTIONS : BRIEF_SECTIONS;
  const known = new Set(base.map((s) => s.key));
  const extra = Object.keys(body)
    .filter((k) => !known.has(k) && !META_KEYS.has(k))
    .map((k) => ({ key: k, title: humanize(k) }));
  return [...base.filter((s) => s.key in body), ...extra];
}

/** True when `body` looks like a structured brief/memo we can render faithfully. */
export function isStructuredBody(body: JsonObject | null | undefined): body is JsonObject {
  if (!body || typeof body !== "object") return false;
  const keys = Object.keys(body).filter((k) => !META_KEYS.has(k));
  if (keys.length === 0) return false;
  return keys.every((k) => toClaims(body[k]) !== null);
}

export interface ReportBodyProps {
  kind: string;
  body: JsonObject | null | undefined;
  markdown: string;
  /** Section heading size: "page" (report reader) or "panel" (inline investigation). */
  density?: "page" | "panel";
  /** Anchor prefix of the matching <Sources idPrefix>. */
  citePrefix?: string;
  className?: string;
}

/**
 * Render a brief/memo body section by section — every claim with its [S#]
 * citations and epistemic marker. Falls back to the markdown field when the
 * body structure is not recognised.
 */
export function ReportBody({ kind, body, markdown, density = "page", citePrefix, className }: ReportBodyProps) {
  if (!isStructuredBody(body)) {
    return markdown ? (
      <Markdown className={className}>{markdown}</Markdown>
    ) : (
      <p className="text-[13.5px] text-ink-3">This report has no body.</p>
    );
  }
  const defs = sectionsFor(kind, body);
  return (
    <div className={clsx("min-w-0", className)}>
      {defs.map((def, i) => {
        const claims = toClaims(body[def.key]) ?? [];
        return (
          <section key={def.key} id={`${citePrefix ?? "r"}-sec-${def.key}`} className={clsx("border-t border-ink", density === "page" ? "pb-7 pt-3" : "pb-5 pt-2.5")}>
            <h3 className="flex items-baseline gap-3">
              <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
              <span className={clsx("font-serif text-ink", density === "page" ? "text-[22px] leading-tight" : "text-[18px] leading-tight")}>{def.title}</span>
              {claims.length > 1 && <span className="label ml-auto tnum text-ink-3">{claims.length}</span>}
            </h3>
            {claims.length === 0 ? (
              <p className="mt-2 flex items-center gap-2 text-[13.5px] text-ink-3">
                <VerificationBadge value="UNKNOWN" />
                {def.empty ?? "Unknown — no evidence was collected for this section. Not a negative finding."}
              </p>
            ) : (
              <ul className="mt-1 max-w-[76ch] divide-y divide-rule/70">
                {claims.map((c, j) => (
                  <ClaimLine key={j} claim={c} numbered={def.numbered} index={j} citePrefix={citePrefix} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
