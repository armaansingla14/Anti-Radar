import clsx from "clsx";
import Link from "next/link";
import { DemoBadge, NewBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { hrefFor } from "@/lib/format";
import type { IntelCard } from "@/lib/types";

export interface FounderRailProps {
  founders: IntelCard[];
  className?: string;
}

/** Compact ranked founder list for the Radar right rail. */
export function FounderRail({ founders, className }: FounderRailProps) {
  return (
    <ol className={clsx("divide-y divide-rule", className)}>
      {founders.map((f) => {
        const href = hrefFor(f.entity_type, f.slug);
        const lead = f.why_surfaced[0]?.title;
        return (
          <li key={f.id} className="group grid grid-cols-[44px_1fr] gap-x-3 py-3 first:pt-0">
            <span
              className="tnum pt-[1px] font-serif text-[26px] leading-none tracking-[-0.02em] text-ink"
              aria-label={`Founder score ${f.score}`}
            >
              {f.score}
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Link
                  href={href}
                  className="font-serif text-[16.5px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline"
                >
                  {f.name}
                </Link>
                <Delta value={f.delta_7d} period="7d" size="xs" />
                {f.is_new && <NewBadge className="ml-auto" />}
              </div>
              {f.one_liner && <p className="mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-ink-2">{f.one_liner}</p>}
              {lead && <p className="mt-1 line-clamp-1 font-mono text-[10.5px] text-ink-3" title={lead}>→ {lead}</p>}
              {f.is_demo && <DemoBadge note={f.data_note} className="mt-1.5" />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
