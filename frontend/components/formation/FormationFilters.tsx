import clsx from "clsx";
import Link from "next/link";
import { THEMES } from "@/components/feed/prose";
import type { FormationCounts } from "@/lib/types";
import { formationListHref, TIERS, type FormationQuery } from "./logic";

const base = "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150 whitespace-nowrap";
const on = "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]";
const off = "text-ink-2 hover:text-ink";

/** Plain-link filters (work without JS): tier | theme | demo. */
export function FormationFilters({ q, counts }: { q: FormationQuery; counts: FormationCounts | null }) {
  const allTiers = !q.tier;
  return (
    <nav aria-label="Formation filters" className="flex flex-wrap items-baseline gap-x-5 gap-y-3 border-b border-rule pb-3">
      <Link href={formationListHref({ ...q, tier: null })} className={clsx(base, allTiers ? on : off)} aria-current={allTiers ? "true" : undefined}>
        All tiers
      </Link>
      {TIERS.map((t) => {
        const active = q.tier === t.key;
        const n = counts?.[t.key];
        return (
          <Link
            key={t.key}
            href={formationListHref({ ...q, tier: active ? null : t.key })}
            className={clsx(base, active ? on : off)}
            aria-current={active ? "true" : undefined}
            title={t.note}
          >
            {t.title}
            {n !== undefined && <span className={clsx("tnum ml-1.5", active ? "text-accent" : "text-ink-3")}>{n}</span>}
          </Link>
        );
      })}
      <span aria-hidden className="h-3 w-px self-center bg-rule-strong" />
      {THEMES.map((t) => {
        const active = q.theme === t.key;
        return (
          <Link
            key={t.key}
            href={formationListHref({ ...q, theme: active ? null : t.key })}
            className={clsx(base, active ? on : off)}
            aria-current={active ? "true" : undefined}
            title={active ? `Clear ${t.label} filter` : `Show only ${t.label}`}
          >
            {t.label}
          </Link>
        );
      })}
      <Link
        href={formationListHref({ ...q, demo: q.demo === true ? null : true })}
        className={clsx(base, "ml-auto", q.demo === true ? on : off)}
        aria-current={q.demo === true ? "true" : undefined}
        title="Fictional teams are labelled DEMO DATA"
      >
        Include demo data
      </Link>
    </nav>
  );
}
