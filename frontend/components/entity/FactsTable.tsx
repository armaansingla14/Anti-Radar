import { VerificationBadge } from "@/components/Badges";
import { SourceLink } from "@/components/SourceLink";
import { fmtDate, isSafeUrl, sourceLabel } from "@/lib/format";
import type { FactField, JsonValue } from "@/lib/types";
import { stageText } from "./primitives";

function renderValue(label: string, v: JsonValue) {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) {
    return <span className="text-ink-3">—</span>;
  }
  if (Array.isArray(v)) return v.map(String).join(" · ");
  if (typeof v === "number") return <span className="tnum">{v.toLocaleString("en-US")}</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string") {
    if (isSafeUrl(v)) {
      return (
        <a href={v} target="_blank" rel="noopener noreferrer nofollow" className="link break-all">
          {v.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
        </a>
      );
    }
    if (/^\d{4}-\d{2}-\d{2}/.test(v)) return <span className="tnum">{fmtDate(v)}</span>;
    if (/stage/i.test(label)) return stageText(v);
    return v;
  }
  return JSON.stringify(v);
}

/** Field-level provenance table: value · VERIFIED/INFERRED/UNKNOWN · source. */
export function FactsTable({ facts }: { facts: FactField[] }) {
  if (facts.length === 0) return <p className="border-y border-rule py-4 text-[13px] text-ink-3">No structured facts on record.</p>;
  const counts = facts.reduce<Record<string, number>>((acc, f) => ((acc[f.status] = (acc[f.status] ?? 0) + 1), acc), {});
  return (
    <div>
      <table className="w-full border-collapse border-t border-ink text-[13px]">
        <caption className="sr-only">Facts with verification status</caption>
        <tbody>
          {facts.map((f) => (
            <tr key={f.label} className="border-b border-rule align-top">
              <th scope="row" className="w-[34%] py-2 pr-3 text-left font-mono text-[10.5px] font-normal uppercase tracking-[0.06em] text-ink-2">
                {f.label}
              </th>
              <td className="py-2 pr-3 text-ink">
                <div className="min-w-0 break-words">{renderValue(f.label, f.value)}</div>
                {(f.note || f.source_url) && (
                  <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-[11.5px] leading-snug text-ink-3">
                    {f.note && <span>{f.note}</span>}
                    {f.source_url && <SourceLink url={f.source_url}>{f.source_type ? sourceLabel(f.source_type) : undefined}</SourceLink>}
                  </div>
                )}
              </td>
              <td className="w-[1%] py-2 text-right">
                <VerificationBadge value={f.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="tnum mt-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
        {(["VERIFIED", "INFERRED", "UNKNOWN"] as const)
          .filter((k) => counts[k])
          .map((k) => `${counts[k]} ${k.toLowerCase()}`)
          .join(" · ")}
      </p>
    </div>
  );
}
