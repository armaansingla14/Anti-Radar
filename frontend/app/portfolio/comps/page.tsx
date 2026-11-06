import type { Metadata } from "next";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/insight/PageHeader";
import { loadPortfolioIndex } from "@/components/entity/data";
import { CompsMapView } from "@/components/portfolio/CompanyPortfolio";
import { MethodNote } from "@/components/portfolio/Views";
import { compCount, portfolioHref, positionFacts } from "@/components/portfolio/logic";
import { getPortfolioComps } from "@/lib/api";

export const metadata: Metadata = { title: "Competitive maps · Portfolio" };

export default async function PortfolioCompsPage() {
  const [maps, index] = await Promise.all([getPortfolioComps(), loadPortfolioIndex()]);
  const stage = new Map((index ?? []).map((i) => [i.company.slug, positionFacts(i.position)]));
  const sorted = [...maps].sort((a, b) => a.company.name.localeCompare(b.company.name));
  const total = maps.reduce((a, m) => a + compCount(m), 0);
  const unique = new Set(maps.flatMap((m) => [...m.direct, ...m.emerging, ...m.adjacent].map((c) => c.company.slug))).size;

  return (
    <div className="pb-24">
      <PageHeader
        kicker={
          <>
            <Link href="/portfolio" className="link">
              Portfolio Pulse
            </Link>{" "}
            · Competitive maps
          </>
        }
        title="Who each portfolio company is compared with, and why"
        dek="Each comp is a real company or project with a public source showing the overlap, a type, a stated confidence and the reason it is comparable. Comparison is judgement — every comp is labelled inference. A comp's material events, and a direct comp's watch events, reach the portfolio company (rule M8). No financial multiples."
        aside={
          <dl className="grid grid-cols-2 gap-x-6">
            <div className="border-t border-ink pt-2">
              <dt className="label">Portfolio companies</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{maps.length}</dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label" title={`${total} comp relationships`}>Distinct comps</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{unique}</dd>
            </div>
          </dl>
        }
      />

      <nav aria-label="Portfolio companies" className="mb-10 flex flex-wrap gap-x-4 gap-y-2 border-y border-rule py-3">
        {sorted.map((m) => (
          <a key={m.company.slug} href={`#comps-${m.company.slug}`} className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 hover:text-accent">
            {m.company.name} <span className="tnum text-ink-3">{compCount(m)}</span>
          </a>
        ))}
      </nav>

      {sorted.length === 0 ? (
        <EmptyState title="No competitive maps.">No comps are configured for the portfolio.</EmptyState>
      ) : (
        <div className="space-y-16">
          {sorted.map((m) => {
            const f = stage.get(m.company.slug);
            return (
              <section key={m.company.slug} id={`comps-${m.company.slug}`} aria-labelledby={`comps-${m.company.slug}-h`} className="scroll-mt-24">
                <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-ink pb-2">
                  <h2 id={`comps-${m.company.slug}-h`} className="font-serif text-[30px] leading-none tracking-[-0.01em] text-ink">
                    <Link href={portfolioHref(m.company.slug)} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
                      {m.company.name}
                    </Link>
                  </h2>
                  <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">
                    {f ? `${f.stage} · ${f.year} · ` : ""}
                    {compCount(m)} comps ·{" "}
                    <Link href={`/companies/${m.company.slug}?tab=comps`} className="hover:text-accent">
                      company page →
                    </Link>
                  </span>
                </div>
                <div className="mt-4">
                  <CompsMapView map={m} />
                </div>
              </section>
            );
          })}
        </div>
      )}
      <MethodNote section="comps" className="mt-12">
        Comps come from the public portfolio configuration, each verified by hand; a short or common name is never resolved by name alone.
      </MethodNote>
    </div>
  );
}
