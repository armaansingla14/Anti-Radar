import type { Metadata } from "next";
import clsx from "clsx";
import Link from "next/link";
import { PageHeader } from "@/components/insight/PageHeader";
import { loadPortfolioComps, loadPortfolioIndex } from "@/components/entity/data";
import { EventList, MethodNote } from "@/components/portfolio/Views";
import { EVENT_TYPES, compIndex, eventFilterHref, eventQuery, parseEventFilters, portfolioSlugs, type EventFilters } from "@/components/portfolio/logic";
import { getPortfolioEvents } from "@/lib/api";

export const metadata: Metadata = { title: "Events · Portfolio" };

const base = "font-mono text-[10.5px] uppercase tracking-[0.08em] whitespace-nowrap transition-colors";
const on = "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[6px]";
const off = "text-ink-2 hover:text-ink";

function Strip({ label, k, options, filters, allLabel = "All" }: { label: string; k: keyof EventFilters; options: { value: string; label: string }[]; filters: EventFilters; allLabel?: string }) {
  return (
    <div className="grid gap-x-4 gap-y-1.5 py-2 sm:grid-cols-[100px_minmax(0,1fr)]">
      <span className="label text-ink-3">{label}</span>
      <nav aria-label={`Filter by ${label.toLowerCase()}`} className="flex flex-wrap gap-x-4 gap-y-2">
        <Link href={eventFilterHref(filters, k, null)} className={clsx(base, filters[k] === null ? on : off)} aria-current={filters[k] === null ? "page" : undefined}>
          {allLabel}
        </Link>
        {options.map((o) => (
          <Link key={o.value} href={eventFilterHref(filters, k, o.value)} className={clsx(base, filters[k] === o.value ? on : off)} aria-current={filters[k] === o.value ? "page" : undefined}>
            {o.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

const LIMIT = 200;

export default async function PortfolioEventsPage({ searchParams }: PageProps<"/portfolio/events">) {
  const filters = parseEventFilters(await searchParams);
  const [index, maps, list] = await Promise.all([loadPortfolioIndex(), loadPortfolioComps(), getPortfolioEvents({ ...eventQuery(filters), limit: LIMIT })]);
  const events = list.items;
  const total = list.total;
  const portfolio = portfolioSlugs(index);
  const comps = compIndex(maps);
  const companies = [...(index ?? [])].sort((a, b) => a.company.name.localeCompare(b.company.name));
  const scope =
    filters.materiality === "ALL" ? "all classes, noise included" : filters.materiality ? filters.materiality.toLowerCase() : "material + watch (noise hidden)";

  return (
    <div className="pb-24">
      <PageHeader
        kicker={
          <>
            <Link href="/portfolio" className="link">
              Portfolio Pulse
            </Link>{" "}
            · Events
          </>
        }
        title="Every typed event, with the rule that classified it"
        dek="Events about portfolio companies and their comps in the 30-day window: one per real-world happening after deduplication, each with its sources, materiality rule and reason. Without a company filter, materiality is the event's own class; filtered by company, competitor events show how they reach that company."
        aside={
          <dl className="grid grid-cols-2 gap-x-6">
            <div className="border-t border-ink pt-2">
              <dt className="label">Shown</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{events.length}</dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label">Matching</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{total}</dd>
            </div>
          </dl>
        }
      />

      <div className="mb-8 border-y border-rule">
        <Strip
          label="Materiality"
          k="materiality"
          filters={filters}
          allLabel="Material + watch"
          options={[
            { value: "MATERIAL", label: "Material" },
            { value: "WATCH", label: "Watch" },
            { value: "NOISE", label: "Noise" },
            { value: "ALL", label: "Include noise" },
          ]}
        />
        <Strip label="Type" k="type" filters={filters} options={EVENT_TYPES.map((t) => ({ value: t, label: t === "COMPETITIVE" ? "Competitive (comp events)" : t }))} />
        <Strip
          label="Relation"
          k="relation"
          filters={filters}
          options={[
            { value: "SELF", label: "Own events" },
            { value: "COMPETITOR", label: "Competitor events" },
          ]}
        />
        <Strip label="Company" k="company" filters={filters} options={companies.map((c) => ({ value: c.company.slug, label: c.company.name }))} />
      </div>

      <p className="label mb-3 text-ink-3">
        {events.length} of {total} · {scope}
        {total > events.length && ` · newest ${LIMIT} shown`}
      </p>
      <EventList
        events={events}
        portfolio={portfolio}
        comps={comps}
        empty="No event matches these filters in the 30-day window. Seed-stage companies often have little public activity — no event is a true result."
      />
      <MethodNote>Types come from normalisation rules (N1–N17); materiality from rules M1–M11. A comp&apos;s event reaches a portfolio company through rule M8.</MethodNote>
    </div>
  );
}
