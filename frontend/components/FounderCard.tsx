import clsx from "clsx";
import Link from "next/link";
import { hrefFor, pad2, personKindLabel } from "@/lib/format";
import type { PersonCard } from "@/lib/types";
import { DemoBadge } from "./Badges";
import { Delta } from "./Delta";
import { HandleLinks } from "./HandleLinks";
import { ScoreNumeral } from "./ScoreNumeral";

export interface FounderCardProps {
  person: PersonCard;
  /** Editorial index ("01"). */
  rank?: number;
  href?: string;
  className?: string;
}

/** Compact founder profile: identity, affiliations, founder score, why-now bullets. */
export function FounderCard({ person, rank, href, className }: FounderCardProps) {
  const target = href ?? hrefFor("person", person.slug);
  const roleLine = [person.current_role ?? person.role, person.current_company].filter(Boolean).join(" · ");
  return (
    <article
      data-card
      data-href={target}
      data-entity-type="person"
      data-entity-id={person.id}
      data-demo={String(person.is_demo)}
      tabIndex={0}
      aria-label={person.name}
      className={clsx("flex h-full flex-col gap-3 border-t border-ink pb-6 pt-3", className)}
    >
      <div className="flex items-center gap-3">
        {rank !== undefined && <span className="tnum font-mono text-[12px] text-ink">{pad2(rank)}</span>}
        <span className="label">{personKindLabel(person)}</span>
        {person.is_demo && <DemoBadge />}
      </div>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="font-serif text-[24px] leading-tight text-ink">
            <Link href={target} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
              {person.name}
            </Link>
          </h3>
          {person.headline && <p className="mt-1 text-[13.5px] leading-snug text-ink-2">{person.headline}</p>}
          {roleLine && <p className="mt-1 text-[12.5px] text-ink">{roleLine}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <ScoreNumeral value={person.founder_score} size="md" label="Founder" />
          <Delta value={person.delta_7d} period="7d" size="xs" />
        </div>
      </div>

      {person.prior_affiliations.length > 0 && (
        <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
          <span className="text-ink-3">Ex — </span>
          {person.prior_affiliations.join(" · ")}
        </p>
      )}

      {person.why_now.length > 0 && (
        <ul className="space-y-1">
          {person.why_now.slice(0, 3).map((w, i) => (
            <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink">
              <span aria-hidden className="text-ink-3">→</span>
              {w}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto flex flex-wrap gap-x-4 gap-y-1 pt-1 font-mono text-[10.5px] text-ink-2">
        <HandleLinks github={person.github_handle} x={person.x_handle} site={person.personal_site} isDemo={person.is_demo} />
        {person.domains.length > 0 && <span className="text-ink-3">{person.domains.slice(0, 3).join(" · ")}</span>}
      </div>
    </article>
  );
}
