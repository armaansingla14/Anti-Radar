import clsx from "clsx";
import Link from "next/link";
import { DemoBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { HandleLinks } from "@/components/HandleLinks";
import { fmtDate, hrefFor, NO_COMPANY_LABEL, pad2 } from "@/lib/format";
import type { PersonCard } from "@/lib/types";

function Handles({ p }: { p: PersonCard }) {
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10.5px] text-ink-2">
      <HandleLinks github={p.github_handle} x={p.x_handle} site={p.personal_site} isDemo={p.is_demo} />
    </span>
  );
}

/** Factual marker: no current company on record (not a prediction that one is coming). */
export function PreCompanyMark({ className }: { className?: string }) {
  return (
    <span
      className={clsx("inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-accent", className)}
      title="No current company on record"
    >
      <span aria-hidden>◆</span> {NO_COMPANY_LABEL}
    </span>
  );
}

/** Dense ranked founder row for the index. */
export function FounderRow({ person: p, rank }: { person: PersonCard; rank: number }) {
  const href = hrefFor("person", p.slug);
  const pre = !p.current_company;
  const role = [p.current_role ?? p.role, p.current_company].filter(Boolean).join(" · ");
  return (
    <li
      data-card
      data-href={href}
      data-entity-type="person"
      data-entity-id={p.id}
      data-demo={String(p.is_demo)}
      tabIndex={0}
      aria-label={p.name}
      className={clsx(
        "group grid grid-cols-[34px_minmax(0,5fr)_minmax(0,6fr)_92px] gap-x-5 border-b border-rule py-4 transition-colors hover:bg-panel/50",
        pre && "shadow-[inset_2px_0_0_var(--color-accent)]",
      )}
    >
      <span className="tnum pl-2 pt-1 font-mono text-[11px] text-ink-3">{pad2(rank)}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Link href={href} tabIndex={-1} className="font-serif text-[21px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 group-hover:underline">
            {p.name}
          </Link>
          {p.is_demo && <DemoBadge />}
        </div>
        {p.headline && <p className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-ink-2">{p.headline}</p>}
        <p className="mt-1.5">
          {pre ? <PreCompanyMark /> : <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink">{role}</span>}
        </p>
        <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          {p.domains.length > 0 && <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{p.domains.join(" · ")}</span>}
          {p.prior_affiliations.length > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
              <span className="text-ink-3">Ex — </span>
              {p.prior_affiliations.slice(0, 3).join(" · ")}
            </span>
          )}
        </div>
      </div>
      <div className="min-w-0">
        {p.why_now.length > 0 ? (
          <ul className="space-y-1">
            {p.why_now.slice(0, 3).map((w, i) => (
              <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink">
                <span aria-hidden className="text-ink-3">→</span>
                <span className="line-clamp-2">{w}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12.5px] text-ink-3">No why-now signals recorded.</p>
        )}
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4">
          <Handles p={p} />
          {p.first_seen_at && <span className="tnum font-mono text-[10px] uppercase text-ink-3">First seen {fmtDate(p.first_seen_at, { year: false })}</span>}
        </div>
      </div>
      <div className="flex flex-col items-end gap-1 pr-2">
        <ScoreNumeral value={p.founder_score} size="md" />
        <Delta value={p.delta_7d} period="7d" size="xs" />
      </div>
    </li>
  );
}

/** Larger spotlight card for people with no company on record. */
export function PreCompanyCard({ person: p, rank }: { person: PersonCard; rank: number }) {
  const href = hrefFor("person", p.slug);
  return (
    <article data-card data-href={href} data-entity-type="person" data-entity-id={p.id} data-demo={String(p.is_demo)} tabIndex={0} aria-label={p.name} className="group flex h-full flex-col gap-3 border-t-2 border-accent pb-6 pt-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-3">
          <span className="tnum font-mono text-[12px] text-ink">{pad2(rank)}</span>
          <PreCompanyMark />
        </span>
        {p.is_demo && <DemoBadge />}
      </div>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="font-serif text-[28px] leading-[1.05] tracking-[-0.01em] text-ink">
            <Link href={href} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 group-hover:underline">
              {p.name}
            </Link>
          </h3>
          {p.headline && <p className="mt-1.5 text-[13.5px] leading-snug text-ink-2">{p.headline}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <ScoreNumeral value={p.founder_score} size="md" />
          <Delta value={p.delta_7d} period="7d" size="xs" />
        </div>
      </div>
      {p.prior_affiliations.length > 0 && (
        <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
          <span className="text-ink-3">Ex — </span>
          {p.prior_affiliations.join(" · ")}
        </p>
      )}
      {p.why_now.length > 0 && (
        <ul className="space-y-1.5">
          {p.why_now.slice(0, 3).map((w, i) => (
            <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink">
              <span aria-hidden className="text-ink-3">→</span>
              {w}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-auto flex flex-wrap items-baseline justify-between gap-2 pt-1">
        <Handles p={p} />
        <Link href={`${href}?investigate=1`} className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-2 hover:text-accent" data-action="investigate">
          Investigate →
        </Link>
      </div>
    </article>
  );
}
