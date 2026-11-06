/** Company-page integration: the PORTFOLIO strip, the "tracked as a comp" line, and the PORTFOLIO / COMPS tab bodies. */
import clsx from "clsx";
import Link from "next/link";
import { Fragment } from "react";
import { Quiet } from "@/components/entity/primitives";
import { fmtDate } from "@/lib/format";
import type { CompsMap, PortfolioCompany, PortfolioIndexItem } from "@/lib/types-portfolio";
import { MaterialityBadge, PriorityBadge } from "./Badges";
import { CompRow, EventList, Ext, QuestionList, Sub } from "./Views";
import { COMP_GROUPS, compCount, compsHref, portfolioHref, positionFacts, type CompLink } from "./logic";

/** One line under the company header for a portfolio company. */
export function PortfolioStrip({ item, className }: { item: PortfolioIndexItem; className?: string }) {
  const f = positionFacts(item.position);
  return (
    <div className={clsx("flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule py-2.5", className)} aria-label="Portfolio status">
      <span className="label shrink-0 text-ink">Portfolio</span>
      <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
        Publicly disclosed Anti Fund portfolio · {f.stage} · {f.year}
      </span>
      <span className="tnum font-mono text-[11px] text-ink-2">
        {item.material} material · {item.watch} watch · {item.open_questions} open questions
      </span>
      {item.priority && item.priority !== "LOW" && <PriorityBadge value={item.priority} />}
      <Link href={portfolioHref(item.company.slug)} className="link ml-auto font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
        Open portfolio view →
      </Link>
    </div>
  );
}

/** "Tracked as a DIRECT comp of Cognition (Anti Fund portfolio) — why" on a comp company's page. */
export function CompOfLine({ links, className }: { links: CompLink[]; className?: string }) {
  if (links.length === 0) return null;
  return (
    <p className={clsx("border-b border-rule py-2.5 text-[12.5px] leading-snug text-ink-2", className)} aria-label="Tracked as a comp">
      <span className="label mr-3 text-ink">Portfolio comp</span>
      Tracked as{" "}
      {links.map((l, i) => (
        <Fragment key={l.portfolio.slug}>
          {i > 0 && (i === links.length - 1 ? " and " : ", ")}
          {/^[aeiou]/i.test(l.comp_type) ? "an" : "a"}{" "}
          <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink">{l.comp_type.replace(/_/g, " ")}</span> comp of{" "}
          <Link href={portfolioHref(l.portfolio.slug)} className="text-ink hover:text-accent">
            {l.portfolio.name}
          </Link>
        </Fragment>
      ))}{" "}
      (Anti Fund portfolio) —{" "}
      <Link href={compsHref(links[0].portfolio.slug)} className="link text-ink" title={links.map((l) => `${l.portfolio.name}: ${l.why_comparable}`).join("\n")}>
        why
      </Link>
      <span className="ml-1 text-ink-3">· {links[0].why_comparable}</span>
    </p>
  );
}

/** PORTFOLIO tab: compact public context, material events, open questions, and the way into the portfolio view. */
export function PortfolioTab({ view, writable, portfolio, comps }: { view: PortfolioCompany | null; writable: boolean; portfolio: ReadonlySet<string>; comps: Map<string, CompLink[]> }) {
  if (!view) return <Quiet>Portfolio data for this company is unavailable right now.</Quiet>;
  const f = positionFacts(view.position);
  const relevant = [...view.events, ...view.competitor_events].filter((e) => e.materiality !== "NOISE");
  const material = relevant.filter((e) => e.materiality === "MATERIAL");
  const shown = (material.length ? material : relevant).slice(0, 6);
  const open = view.questions.filter((q) => q.status === "OPEN");
  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-t border-ink pt-3">
        <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
          Publicly disclosed Anti Fund portfolio company · stage {f.stage} · year {f.year} · round {f.round} ·{" "}
          <Ext url={view.position.source_url} className="text-ink-2">
            antifund.com
          </Ext>
          {view.position.source_checked_at && ` checked ${fmtDate(view.position.source_checked_at)}`}
        </p>
        <Link
          href={portfolioHref(view.company.slug)}
          className="border border-ink bg-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-paper transition-colors hover:border-accent hover:bg-accent"
        >
          Open portfolio view →
        </Link>
      </div>
      <p className="-mt-6 text-[12px] text-ink-3">Ownership, cost basis, marks and other private fields are not shown in public mode.</p>
      <div>
        <Sub right={material.length ? `${material.length} material` : `${relevant.length} to watch`}>{material.length ? "Material events" : "Events to watch"}</Sub>
        <div className="mt-2">
          <EventList
            events={shown}
            portfolio={portfolio}
            comps={comps}
            empty="No material or watch event in the 30-day window — a true result for the public sources collected, not padded."
          />
        </div>
      </div>
      <div>
        <Sub right={open.length}>Open questions</Sub>
        <div className="mt-2">
          <QuestionList questions={open} writable={writable} />
        </div>
      </div>
    </div>
  );
}

/** COMPS tab / comps-page section: the company's competitive map. No financial multiples. */
export function CompsMapView({ map, headingLevel = "h3" }: { map: CompsMap; headingLevel?: "h2" | "h3" }) {
  const H = headingLevel;
  if (compCount(map) === 0) return <Quiet>No comps configured for this company.</Quiet>;
  return (
    <div className="space-y-6">
      {COMP_GROUPS.map((g) => {
        const list = map[g.key];
        if (list.length === 0) return null;
        return (
          <div key={g.key}>
            <H className="label flex items-baseline gap-2 text-ink">
              {g.label} <span className="tnum text-ink-3">{list.length}</span>
              <span className="normal-case tracking-normal text-ink-3">— {g.note}</span>
            </H>
            <ul className="mt-1.5 border-t border-ink">
              {list.map((c) => (
                <CompRow key={c.company.id} comp={c} />
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export { MaterialityBadge };
