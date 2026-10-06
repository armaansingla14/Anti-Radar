import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { BriefActions } from "@/components/feed/BriefActions";
import { EntityLinks } from "@/components/feed/EntityLinks";
import { GeneratorNote } from "@/components/feed/GeneratorNote";
import { StrangeSignalDispatch } from "@/components/feed/StrangeSignalDispatch";
import { longDate } from "@/components/feed/prose";
import { getBrief } from "@/lib/api";
import { hrefFor, isSafeUrl, pad2 } from "@/lib/format";
import type { IntelCard, PaperCard } from "@/lib/types";
import "./print.css";

export const metadata: Metadata = { title: "Morning brief" };

function Heading({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-4 border-b border-ink pb-1.5">
      <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink">{children}</h2>
      {right && <span className="label text-ink-3">{right}</span>}
    </div>
  );
}

function EntityRows({ cards }: { cards: IntelCard[] }) {
  if (cards.length === 0) return <p className="text-[13px] text-ink-3">None cleared the bar today.</p>;
  return (
    <ol className="divide-y divide-rule">
      {cards.map((c, i) => {
        const href = hrefFor(c.entity_type, c.slug);
        return (
          <li key={c.id} className="grid grid-cols-[22px_minmax(0,1fr)_auto] gap-x-3 py-3 first:pt-0">
            <span className="tnum pt-[3px] font-mono text-[10.5px] text-ink-3">{pad2(i + 1)}</span>
            <div className="min-w-0">
              <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Link href={href} className="font-serif text-[17px] leading-tight text-ink decoration-accent underline-offset-4 hover:underline">
                  {c.name}
                </Link>
                {c.is_demo && <DemoBadge note={c.data_note} />}
              </p>
              <p className="label mt-0.5 text-ink-3">{c.kind_label}</p>
              {c.one_liner && <p className="mt-1 text-[12.5px] leading-snug text-ink-2">{c.one_liner}</p>}
              {c.why_surfaced[0] && <p className="mt-1 text-[12.5px] leading-snug text-ink">→ {c.why_surfaced[0].title}</p>}
            </div>
            <div className="text-right">
              <span className="tnum block font-serif text-[26px] leading-none text-ink">{c.score}</span>
              <Delta value={c.delta_7d} period="7d" size="xs" />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Research({ papers, llm }: { papers: PaperCard[]; llm: boolean }) {
  if (papers.length === 0) return <p className="text-[13px] text-ink-3">No thesis-relevant papers in the last 30 days.</p>;
  return (
    <ol className="space-y-5">
      {papers.map((p, i) => (
        <li key={p.id} className="grid grid-cols-[22px_minmax(0,1fr)] gap-x-3">
          <span className="tnum pt-[4px] font-mono text-[10.5px] text-ink-3">{pad2(i + 1)}</span>
          <div className="min-w-0">
            <p className="font-serif text-[17px] leading-snug text-ink">
              {isSafeUrl(p.url) ? (
                <a href={p.url} target="_blank" rel="noopener noreferrer nofollow" className="decoration-accent underline-offset-4 hover:underline">
                  {p.title}
                </a>
              ) : (
                p.title
              )}
              {p.is_demo && <DemoBadge className="ml-2 align-middle" />}
            </p>
            <p className="mt-0.5 text-[12.5px] text-ink-2">
              {p.authors.slice(0, 4).join(", ")}
              {p.authors.length > 4 && ` + ${p.authors.length - 4}`}
              <span className="ml-2 font-mono text-[10.5px] text-ink-3">
                arXiv {p.arxiv_id} · {p.categories.join(" ")}
                {p.code_urls.length > 0 && " · code released"}
              </span>
            </p>
            {p.why_it_matters && (
              <p className="mt-1.5 text-[13px] leading-relaxed text-ink">
                <EpistemicBadge value={llm ? "MODEL_OPINION" : "INFERENCE"} compact className="mr-1.5 align-[1px]" />
                {p.why_it_matters}
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

export default async function BriefPage() {
  const brief = await getBrief();
  const llm = brief.generator === "llm" || brief.generator === "cached";
  const anyDemo =
    brief.founders_to_know.some((c) => c.is_demo) ||
    brief.companies_to_watch.some((c) => c.is_demo) ||
    brief.research.some((p) => p.is_demo) ||
    Boolean(brief.strange_signal?.is_demo);

  return (
    <div className="brief-print mx-auto max-w-[860px] pb-24">
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4 print:hidden">
        <Link href="/" className="label text-ink-2 hover:text-ink">
          ← Radar
        </Link>
        <BriefActions date={brief.date} />
      </div>

      {/* ── masthead ── */}
      <header className="border-t-[3px] border-ink pt-1">
        <div className="border-t border-ink pt-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
            <p className="font-mono text-[11.5px] uppercase tracking-[0.2em] text-ink">
              Anti <span className="text-accent">Radar</span> Morning Brief
            </p>
            <p className="label text-ink-3">Anti Fund · Internal · Not for distribution</p>
          </div>
          <h1 className="mt-4 font-serif text-[46px] leading-[1.02] tracking-[-0.018em] text-ink">
            <time dateTime={brief.date}>{longDate(brief.date, { year: true })}</time>
          </h1>
          <p className="mt-1 font-serif text-[17px] italic text-ink-2">Conviction Before Consensus.</p>
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-rule py-2">
          <GeneratorNote generator={brief.generator} />
          {anyDemo && (
            <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3">
              <DemoBadge /> entries are fictional examples
            </span>
          )}
        </div>
      </header>

      {brief.narrative && (
        <section className="mt-8">
          <p className="label mb-2 flex items-center gap-2 text-ink">
            Summary <EpistemicBadge value={brief.narrative.epistemic} />
          </p>
          <p className="font-serif text-[20px] leading-[1.5] text-ink">
            {brief.narrative.text}
          </p>
        </section>
      )}

      {/* ── what changed ── */}
      <section className="mt-12">
        <Heading right={`${brief.what_changed.length} items`}>What changed</Heading>
        {brief.what_changed.length > 0 ? (
          <ol className="divide-y divide-rule">
            {brief.what_changed.map((item) => (
              <li key={item.n} className="grid grid-cols-[52px_minmax(0,1fr)] gap-x-4 py-4 first:pt-1">
                <span className="tnum font-serif text-[34px] leading-none tracking-[-0.02em] text-ink">{pad2(item.n)}</span>
                <div className="min-w-0">
                  <p className="text-[15px] leading-relaxed text-ink">{item.text}</p>
                  <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                    <EntityLinks entities={item.entity_refs} limit={6} />
                    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3" title="Distinct source signals cited for this item">
                      {item.evidence.length} evidence {item.evidence.length === 1 ? "signal" : "signals"}
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-[14px] text-ink-3">Nothing on the radar changed materially since the last brief.</p>
        )}
      </section>

      <div className="mt-12 grid gap-x-12 gap-y-12 md:grid-cols-2 print:grid-cols-2">
        <section>
          <Heading right="Founder Score">Founders to know</Heading>
          <EntityRows cards={brief.founders_to_know} />
        </section>
        <section>
          <Heading right="Anti Score">Companies to watch</Heading>
          <EntityRows cards={brief.companies_to_watch} />
        </section>
      </div>

      <section className="mt-12">
        <Heading>Research worth reading</Heading>
        <Research papers={brief.research} llm={llm} />
      </section>

      <section className="mt-12">
        <Heading>Strange signal</Heading>
        {brief.strange_signal ? (
          <div className="border-l-2 border-accent pl-5">
            <StrangeSignalDispatch signal={brief.strange_signal} index={1} variant="full" />
          </div>
        ) : (
          <p className="text-[14px] text-ink-3">No unusual pattern cleared the detectors today.</p>
        )}
      </section>

      <footer className="mt-14 flex flex-wrap justify-between gap-x-6 gap-y-1 border-t border-ink pt-2 font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink-3">
        <span>Anti Radar · Morning brief · {brief.date}</span>
        <span>Signals are not facts until sourced · Scores are explainable in-app</span>
      </footer>
    </div>
  );
}
