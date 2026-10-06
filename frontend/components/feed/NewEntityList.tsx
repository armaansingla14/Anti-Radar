import Link from "next/link";
import { DemoBadge } from "@/components/Badges";
import { fmtDate, hrefFor } from "@/lib/format";
import type { CompanySummary, PersonCard } from "@/lib/types";

type Entity = CompanySummary | PersonCard;

function isPerson(e: Entity): e is PersonCard {
  return "founder_score" in e;
}

/** Compact list of entities that entered the radar recently. */
export function NewEntityList({ entities }: { entities: Entity[] }) {
  return (
    <ul className="grid gap-x-10 md:grid-cols-2">
      {entities.map((e) => {
        const person = isPerson(e);
        const href = hrefFor(person ? "person" : "company", e.slug);
        const score = person ? e.founder_score : e.score;
        const sub = person ? e.headline : e.one_liner;
        const kind = person ? (e.is_founder ? "Founder" : "Person") : e.kind === "stealth" ? "Stealth company" : e.kind === "project" ? "Project" : "Company";
        return (
          <li key={e.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 border-t border-rule py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Link href={href} className="truncate font-serif text-[17px] leading-tight text-ink decoration-accent underline-offset-4 hover:underline">
                  {e.name}
                </Link>
                <span className="label text-ink-3">{kind}</span>
                {e.is_demo && <DemoBadge />}
              </div>
              {sub && <p className="mt-0.5 line-clamp-1 text-[12.5px] text-ink-2" title={sub}>{sub}</p>}
            </div>
            <div className="text-right">
              <span className="tnum block font-serif text-[22px] leading-none text-ink">{score ?? "—"}</span>
              <span className="label text-ink-3">{e.first_seen_at ? fmtDate(e.first_seen_at, { year: false }) : "—"}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
