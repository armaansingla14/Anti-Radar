import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { FailureStatusChip, SeverityChip } from "@/components/agents/Chips";
import { failureDisplay, failureHref, filterHref, originLabel, parseFailureFilters, type FailureFilters } from "@/components/agents/logic";
import { getAgentFailures, getAgents } from "@/lib/api";
import type { FailureOut } from "@/lib/types-agents";

export const metadata: Metadata = { title: "Failure ledger · Agents" };

const base = "font-mono text-[10.5px] uppercase tracking-[0.08em] whitespace-nowrap transition-colors";
const on = "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[6px]";
const off = "text-ink-2 hover:text-ink";

function Strip({ label, k, values, filters, counts }: { label: string; k: keyof FailureFilters; values: string[]; filters: FailureFilters; counts: Map<string, number> }) {
  return (
    <div className="grid gap-x-4 gap-y-1.5 py-2 sm:grid-cols-[90px_minmax(0,1fr)]">
      <span className="label text-ink-3">{label}</span>
      <nav aria-label={`Filter by ${label.toLowerCase()}`} className="flex flex-wrap gap-x-4 gap-y-2">
        <Link href={filterHref(filters, k, null)} className={clsx(base, filters[k] === null ? on : off)} aria-current={filters[k] === null ? "page" : undefined}>
          All
        </Link>
        {values.map((v) => (
          <Link key={v} href={filterHref(filters, k, v)} className={clsx(base, filters[k] === v ? on : off)} aria-current={filters[k] === v ? "page" : undefined}>
            {v.replace(/_/g, " ")}
            <span className="tnum ml-1 text-ink-3">{counts.get(v) ?? 0}</span>
          </Link>
        ))}
      </nav>
    </div>
  );
}

const countBy = (xs: FailureOut[], f: (x: FailureOut) => string) => {
  const m = new Map<string, number>();
  for (const x of xs) m.set(f(x), (m.get(f(x)) ?? 0) + 1);
  return m;
};

export default async function FailuresPage({ searchParams }: PageProps<"/agents/failures">) {
  const filters = parseFailureFilters(await searchParams);
  const [all, ov] = await Promise.all([getAgentFailures(), getAgents().catch(() => null)]);
  const shown = await (filters.status || filters.category || filters.agent
    ? getAgentFailures({ status: filters.status ?? undefined, category: filters.category ?? undefined, agent: filters.agent ?? undefined })
    : Promise.resolve(all));
  const statuses = ["OPEN", "CONFIRMED", "FIXED", "DISMISSED"];
  const categories = [...new Set([...(ov?.taxonomy.map((t) => t.category) ?? []), ...all.map((f) => f.category)])].filter((c) => all.some((f) => f.category === c));
  const agents = [...new Set(all.map((f) => f.agent))].sort();

  return (
    <div className="pb-24">
      <PageHeader
        kicker={
          <>
            <Link href="/agents" className="link">
              Agents
            </Link>{" "}
            · Failure ledger
          </>
        }
        title="Where the research system was wrong"
        dek="Every real failure we found — by hand audit, the automatic evaluator or a reviewer — with the evidence that it was wrong, how it was resolved, and the regression case or test that pins the fix. Nothing here is hypothetical."
        aside={
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <div className="border-t border-ink pt-2">
              <dt className="label">Failures</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{all.length}</dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label" title="A YAML regression case or a pytest test pins the fix">Regression pinned</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{all.filter((f) => f.regression_test || f.regression_case).length}</dd>
            </div>
          </dl>
        }
      />

      <div className="mb-8 border-y border-rule">
        <Strip label="Status" k="status" values={statuses} filters={filters} counts={countBy(all, (f) => f.status)} />
        <Strip label="Category" k="category" values={categories} filters={filters} counts={countBy(all, (f) => f.category)} />
        <Strip label="Agent" k="agent" values={agents} filters={filters} counts={countBy(all, (f) => f.agent)} />
      </div>

      <SectionLabel index={1} right={<span className="tnum">{shown.length} of {all.length}</span>}>
        Ledger
      </SectionLabel>
      {shown.length === 0 ? (
        <EmptyState className="mt-6" title="No failures match these filters." action={<Link href="/agents/failures" className="link font-mono text-[11px] uppercase tracking-[0.08em]">Clear filters</Link>} />
      ) : (
        <ol className="mt-5 border-t border-ink">
          {shown.map((f) => (
            <li key={f.number} className="grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-1.5 border-b border-rule py-3.5 md:grid-cols-[118px_minmax(0,1fr)_200px]">
              <Link href={failureHref(f.number)} className="tnum pt-0.5 font-mono text-[11px] text-ink hover:text-accent">
                {failureDisplay(f)}
              </Link>
              <div className="min-w-0">
                <Link href={failureHref(f.number)} className="text-[14px] leading-snug text-ink decoration-accent underline-offset-4 hover:underline">
                  {f.claim_text}
                </Link>
                <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
                  {f.agent} · {f.category.replace(/_/g, " ")} · {originLabel(f.origin)}
                  {f.regression_test || f.regression_case ? " · regression pinned" : " · no regression test"}
                </p>
              </div>
              <div className="flex flex-wrap items-start gap-2 md:justify-end">
                <SeverityChip value={f.severity} />
                <FailureStatusChip value={f.status} />
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
