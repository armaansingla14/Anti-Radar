import Link from "next/link";
import { DemoBadge } from "@/components/Badges";
import { hrefFor } from "@/lib/format";
import type { FormationMember } from "@/lib/types";
import { SelfReported } from "./Labels";
import { cleanAffiliations } from "./logic";

function Monogram({ name }: { name: string }) {
  const letters = name
    .replace(/^@/, "")
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  return (
    <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center border border-rule-strong font-serif text-[16px] text-ink-2">
      {letters || "·"}
    </span>
  );
}

/** TEAM — member cards linking to founder reports; affiliations are labelled self-reported. */
export function TeamGrid({ members }: { members: FormationMember[] }) {
  return (
    <ul className="grid gap-x-8 gap-y-0 md:grid-cols-2 xl:grid-cols-3">
      {members.map((m) => {
        const href = hrefFor("person", m.slug || m.id);
        const aff = cleanAffiliations(m.affiliations, 4);
        return (
          <li key={m.id} data-card data-href={href} tabIndex={0} aria-label={m.name} className="group flex gap-3.5 border-t border-rule py-4">
            <Monogram name={m.name} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <Link
                  href={href}
                  tabIndex={-1}
                  data-action="open"
                  className="min-w-0 truncate font-serif text-[19px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 group-hover:underline"
                >
                  {m.name}
                </Link>
                <span className="tnum shrink-0 font-mono text-[11px] text-ink-2" title="Founder Score">
                  {m.founder_score === null ? "—" : Math.round(m.founder_score)}
                  <span className="ml-1 text-[9.5px] uppercase tracking-[0.06em] text-ink-3">FS</span>
                </span>
              </div>
              {m.headline && <p className="mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-ink-2">{m.headline}</p>}
              {aff.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-mono text-[10.5px] uppercase tracking-[0.04em] text-ink">{aff.join(" · ")}</span>
                  <SelfReported />
                </div>
              )}
              {m.is_demo && <DemoBadge className="mt-2" />}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
