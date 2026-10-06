import clsx from "clsx";
import Link from "next/link";
import { fmtCompact, fmtDate, fmtNum, fmtSigned, hrefFor, humanize, pad2 } from "@/lib/format";
import type { CompanySummary } from "@/lib/types";
import { DemoBadge } from "./Badges";
import { Delta } from "./Delta";

export interface CompanyRowProps {
  company: CompanySummary;
  rank?: number;
  href?: string;
}

const th = "label whitespace-nowrap px-2 py-2 text-left font-normal";
const td = "px-2 py-2.5 align-top";

/** Dense list row. Use inside <CompanyTable> (or a table with the same columns). */
export function CompanyRow({ company: c, rank, href }: CompanyRowProps) {
  const target = href ?? hrefFor("company", c.slug);
  return (
    <tr data-card data-href={target} tabIndex={0} className="group border-b border-rule transition-colors hover:bg-panel/60">
      <td className={clsx(td, "tnum w-10 font-mono text-[11px] text-ink-3")}>{rank !== undefined ? pad2(rank) : ""}</td>
      <td className={clsx(td, "min-w-[220px]")}>
        <div className="flex items-center gap-2">
          <Link href={target} tabIndex={-1} className="font-serif text-[17px] leading-tight text-ink group-hover:underline decoration-accent decoration-1 underline-offset-4">
            {c.name}
          </Link>
          {c.watched && <span className="text-[9px] text-accent" title="On your watchlist">●</span>}
          {c.is_demo && <DemoBadge />}
        </div>
        {c.one_liner && <p className="mt-0.5 line-clamp-1 max-w-[52ch] text-[12.5px] text-ink-2">{c.one_liner}</p>}
      </td>
      <td className={clsx(td, "whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2")}>
        {humanize(c.kind)}
        <div className="text-ink-3">{humanize(c.stage)}</div>
      </td>
      <td className={clsx(td, "max-w-[200px] font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink-2")}>
        {c.thesis_tags.slice(0, 2).map((t) => t.label).join(" · ") || "—"}
      </td>
      <td className={clsx(td, "tnum text-right font-serif text-[22px] leading-none text-ink")}>{c.score ?? "—"}</td>
      <td className={clsx(td, "text-right")}>
        <Delta value={c.delta_7d} size="xs" />
      </td>
      <td className={clsx(td, "tnum text-right font-mono text-[11px]", c.attention_gap !== null && c.attention_gap > 0 ? "text-ink" : "text-ink-2")}>
        {c.attention_gap === null ? "—" : fmtSigned(c.attention_gap, 0)}
      </td>
      <td className={clsx(td, "tnum text-right font-mono text-[11px] text-ink-2")}>{c.collision === null ? "—" : fmtNum(c.collision, 2)}</td>
      <td className={clsx(td, "tnum text-right font-mono text-[11px] text-ink-2")}>
        {c.stars_30d === null ? "—" : `+${fmtCompact(c.stars_30d)}`}
      </td>
      <td className={clsx(td, "tnum whitespace-nowrap text-right font-mono text-[10.5px] uppercase text-ink-3")}>{fmtDate(c.first_seen_at, { year: false })}</td>
    </tr>
  );
}

export interface CompanyTableProps {
  companies: CompanySummary[];
  /** Rank offset for pagination (first row = start + 1). */
  start?: number;
  caption?: string;
  className?: string;
}

/** Dense, keyboard-navigable company list. */
export function CompanyTable({ companies, start = 0, caption, className }: CompanyTableProps) {
  return (
    <div className={clsx("overflow-x-auto", className)}>
      <table className="w-full border-collapse border-t border-ink text-[13px]">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-ink">
            <th className={th} scope="col">#</th>
            <th className={th} scope="col">Company</th>
            <th className={th} scope="col">Kind / stage</th>
            <th className={th} scope="col">Thesis</th>
            <th className={clsx(th, "text-right")} scope="col">Score</th>
            <th className={clsx(th, "text-right")} scope="col">Δ 7d</th>
            <th className={clsx(th, "text-right")} scope="col" title="Technical percentile minus attention percentile">Gap</th>
            <th className={clsx(th, "text-right")} scope="col">Collision</th>
            <th className={clsx(th, "text-right")} scope="col">Stars 30d</th>
            <th className={clsx(th, "text-right")} scope="col">First seen</th>
          </tr>
        </thead>
        <tbody>
          {companies.map((c, i) => (
            <CompanyRow key={c.id} company={c} rank={start + i + 1} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
