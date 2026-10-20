import type { Metadata } from "next";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { themeLabel } from "@/components/feed/prose";
import { FormationCardView } from "@/components/formation/FormationCardView";
import { FormationFilters } from "@/components/formation/FormationFilters";
import { emptyTierLine, formationDeck, parseFormationQuery, tierLayout, TIERS } from "@/components/formation/logic";
import { ApiError, getFormation } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { FormationCard, FormationFeed } from "@/lib/types";

export const metadata: Metadata = { title: "Formation Radar" };

function AsOf({ feed }: { feed: FormationFeed }) {
  return (
    <p className="font-mono text-[10.5px] uppercase leading-relaxed tracking-[0.08em] text-ink-3">
      {feed.is_snapshot ? (
        <span className="text-ink-2">Snapshot · data as of {fmtDate(feed.as_of)}</span>
      ) : (
        <span>
          <span className="text-ink-2">Live</span> · computed {fmtDate(feed.as_of)}
        </span>
      )}
      <span className="ml-3">Deterministic scoring · no language model in the score</span>
    </p>
  );
}

export default async function FormationPage({ searchParams }: PageProps<"/formation">) {
  const q = parseFormationQuery(await searchParams);

  let feed: FormationFeed | null = null;
  let rejected: string | null = null;
  try {
    feed = await getFormation({ tier: q.tier ?? undefined, theme: q.theme ?? undefined, include_demo: q.demo ?? undefined });
  } catch (e) {
    if (!(e instanceof ApiError && (e.status === 400 || e.status === 422))) throw e;
    rejected = e.detail;
  }

  const filterName = [q.theme && `in ${themeLabel(q.theme)}`, q.demo === true && "including demo data"].filter(Boolean).join(", ");
  const c = feed?.counts ?? null;
  const layout = c ? tierLayout(c, q.tier) : { sections: [], empty: [] };
  const tiers = TIERS.filter((t) => layout.sections.includes(t.key));
  // Header strip: big numerals for non-empty tiers only; empty tiers collapse into one quiet line.
  const stripTiers = c ? TIERS.filter((t) => c[t.key] > 0) : [];
  const stripEmpty = c && c.total > 0 ? emptyTierLine(TIERS.filter((t) => !(c[t.key] > 0)).map((t) => t.key)) : "";

  return (
    <div className="pb-24">
      <header className="grid gap-x-12 gap-y-6 pb-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="min-w-0">
          <p className="label mb-3 text-ink">Formation Radar</p>
          <h1 className="max-w-[20ch] font-serif text-[46px] leading-[1.0] tracking-[-0.02em] text-ink md:text-[58px]">
            Technical teams before the company announcement.
          </h1>
          <p className="mt-4 max-w-[60ch] font-serif text-[20px] italic leading-snug text-ink-2 md:text-[22px]">
            {feed ? formationDeck(feed.counts) : "The radar could not apply these filters."}
            {filterName && feed && feed.counts.total > 0 && <span className="not-italic text-ink-3"> ({filterName})</span>}
          </p>
          <div className="mt-4">{feed && <AsOf feed={feed} />}</div>
        </div>
        {c && c.total > 0 && (
          <div className="lg:pb-[30px]">
            <dl className="flex gap-8 lg:justify-end">
              {stripTiers.map((t) => (
                <div key={t.key} className="border-t border-ink pt-2.5">
                  <dt className="label">{t.title}</dt>
                  <dd className={`tnum mt-1 font-serif text-[40px] leading-none tracking-[-0.02em] ${t.key === "high" ? "text-accent" : "text-ink"}`}>
                    <a href={`#tier-${t.key}`} className="hover:underline decoration-1 underline-offset-4">
                      {c[t.key]}
                    </a>
                  </dd>
                </div>
              ))}
            </dl>
            {stripEmpty && (
              <p data-tier-empty className="mt-3 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3 lg:text-right">
                {stripEmpty}
              </p>
            )}
          </div>
        )}
      </header>

      <FormationFilters q={q} counts={c} />

      <p className="mt-3 max-w-[100ch] text-[12.5px] leading-relaxed text-ink-3">
        <span className="text-ink-2">Formation Score</span> (0–100) = founder quality 25 · team convergence 25 · technical activity 20 ·
        formation evidence 20 · novelty / attention gap 10. Labels describe signals, not outcomes — nobody is described as a founder
        unless a founder relation is on record. GitHub company and bio fields are self-reported.
        <span className="ml-2 font-mono text-[10.5px]">J / K move · O open · Y why · W watch</span>
      </p>

      {rejected && (
        <div className="mt-10">
          <EmptyState
            title="That filter is not on the map."
            action={
              <Link href="/formation" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                ← Full formation radar
              </Link>
            }
          >
            {rejected}
          </EmptyState>
        </div>
      )}

      {feed && feed.counts.total === 0 && !q.tier && (
        <div className="mt-10">
          <EmptyState
            title={filterName ? `No formation signals ${filterName} in this period.` : "No formation signals detected in this period."}
            action={
              filterName ? (
                <Link href="/formation" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                  ← Full formation radar
                </Link>
              ) : (
                <Link href="/founders?pre=1" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                  People with no company on record →
                </Link>
              )
            }
          >
            Candidates appear when a young GitHub org or repository, a first job post, a new domain or a shared new project links
            technical people — with dated evidence from at least one source family.
          </EmptyState>
        </div>
      )}

      {feed && (feed.counts.total > 0 || q.tier) && (
        <div className="mt-12 space-y-16">
          {tiers.map((t, i) => {
            const cards: FormationCard[] = feed[t.key];
            return (
              <section key={t.key} id={`tier-${t.key}`} aria-labelledby={`tier-${t.key}-title`} className="scroll-mt-24">
                <SectionLabel
                  index={i + 1}
                  id={`tier-${t.key}-title`}
                  right={
                    <span className="tnum">
                      {cards.length} {cards.length === 1 ? "candidate" : "candidates"}
                    </span>
                  }
                >
                  {t.title}
                </SectionLabel>
                <p className="mb-6 mt-2 font-mono text-[10.5px] text-ink-3">{t.note}</p>
                {cards.length > 0 ? (
                  <div>
                    {cards.map((card, j) => (
                      <FormationCardView key={card.id} card={card} rank={j + 1} />
                    ))}
                  </div>
                ) : (
                  <p className="border-y border-rule py-4 text-[13px] text-ink-3">
                    No {t.title.toLowerCase()} candidates{filterName ? ` ${filterName}` : ""} in this period.
                  </p>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
