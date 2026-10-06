import type { Metadata } from "next";
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { DemoBadge, EpistemicBadge } from "@/components/Badges";
import { Delta } from "@/components/Delta";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { DigestRow } from "@/components/feed/DigestRow";
import { GeneratorNote } from "@/components/feed/GeneratorNote";
import { NewEntityList } from "@/components/feed/NewEntityList";
import { PaperCard } from "@/components/feed/PaperCard";
import { StrangeSignalDispatch } from "@/components/feed/StrangeSignalDispatch";
import { longDate } from "@/components/feed/prose";
import { getDaily } from "@/lib/api";
import { fmtDateTime, hrefFor } from "@/lib/format";

export const metadata: Metadata = { title: "Daily" };

const WINDOWS = [
  { key: "", label: "Since last visit" },
  { key: "1", label: "24 hours" },
  { key: "3", label: "3 days" },
  { key: "7", label: "7 days" },
];

/** ISO instant N days before now (request time). */
function sinceDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

function Section({ n, title, right, children, id }: { n: number; title: string; right?: ReactNode; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="mt-16">
      <SectionLabel index={n} id={id} right={right}>
        {title}
      </SectionLabel>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return <p className="border-t border-rule py-5 text-[13.5px] text-ink-3">{children}</p>;
}

export default async function DailyPage({ searchParams }: PageProps<"/daily">) {
  const sp = await searchParams;
  const raw = Array.isArray(sp.window) ? sp.window[0] : sp.window;
  const win = WINDOWS.find((w) => w.key && w.key === raw)?.key ?? "";
  const since = win ? sinceDaysAgo(Number(win)) : undefined;

  const daily = await getDaily(since ? { since } : undefined);
  const llm = daily.summary?.generator === "llm" || daily.summary?.generator === "cached";

  return (
    <div className="pb-24">
      <header className="grid gap-x-12 gap-y-6 border-b border-ink pb-8 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0">
          <p className="label mb-3 text-ink">Daily radar · {longDate(daily.date)}</p>
          <h1 className="font-serif text-[48px] leading-[1.02] tracking-[-0.018em] text-ink md:text-[58px]">What changed since yesterday?</h1>
          <p className="mt-4 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2">
            Changes since <time dateTime={daily.since}>{fmtDateTime(daily.since)}</time>
          </p>
          <nav aria-label="Time window" className="mt-4 flex flex-wrap gap-x-5 gap-y-2">
            {WINDOWS.map((w) => {
              const active = w.key === win;
              return (
                <Link
                  key={w.key || "last"}
                  href={w.key ? `/daily?window=${w.key}` : "/daily"}
                  aria-current={active ? "true" : undefined}
                  className={clsx(
                    "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150",
                    active ? "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]" : "text-ink-2 hover:text-ink",
                  )}
                >
                  {w.label}
                </Link>
              );
            })}
          </nav>
          <ol aria-label="In this edition" className="mt-8 grid max-w-[640px] grid-cols-2 gap-x-8 border-t border-rule pt-3 sm:grid-cols-3">
            {[
              ["d-founders", "Founders", daily.founders.length],
              ["d-companies", "Companies", daily.companies.length],
              ["d-papers", "Papers", daily.papers.length],
              ["d-strange", "Strange signals", daily.strange_signals.length],
              ["d-watch", "Watchlist", daily.watchlist_changes.length],
              ["d-new", "New this week", daily.new_entities.length],
            ].map(([id, label, n], i) => (
              <li key={id as string}>
                <a href={`#${id}`} className="group flex items-baseline gap-2 py-1 text-[13px] text-ink-2 hover:text-ink">
                  <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                  <span className="group-hover:underline group-hover:decoration-accent group-hover:underline-offset-4">{label}</span>
                  <span className={clsx("tnum ml-auto font-mono text-[11px]", n ? "text-ink" : "text-ink-3")}>{n}</span>
                </a>
              </li>
            ))}
          </ol>
        </div>
        <div className="min-w-0 lg:border-l lg:border-rule lg:pl-8">
          <p className="label mb-2 flex items-center gap-2 text-ink">
            The short version {daily.summary && <EpistemicBadge value={daily.summary.epistemic} />}
          </p>
          {daily.summary ? (
            <>
              <p className="font-serif text-[17.5px] leading-[1.5] text-ink">{daily.summary.text}</p>
              <GeneratorNote generator={daily.summary.generator} model={daily.summary.model} className="mt-3" />
            </>
          ) : (
            <p className="text-[13.5px] text-ink-3">No summary — nothing on the radar changed enough to narrate.</p>
          )}
        </div>
      </header>

      <Section n={1} id="d-founders" title="Five founders to know" right={<span className="tnum">{daily.founders.length}</span>}>
        {daily.founders.length > 0 ? (
          daily.founders.map((c, i) => <DigestRow key={c.id} card={c} index={i + 1} />)
        ) : (
          <Quiet>No founder&apos;s score rose and no new founder signals arrived in this window.</Quiet>
        )}
      </Section>

      <Section n={2} id="d-companies" title="Five companies accelerating" right={<span className="tnum">{daily.companies.length}</span>}>
        {daily.companies.length > 0 ? (
          daily.companies.map((c, i) => <DigestRow key={c.id} card={c} index={i + 1} />)
        ) : (
          <Quiet>No company accelerated in this window.</Quiet>
        )}
      </Section>

      <Section n={3} id="d-papers" title="Three research papers that matter" right={<span className="tnum">{daily.papers.length}</span>}>
        {daily.papers.length > 0 ? (
          <div className="grid gap-x-8 gap-y-10 md:grid-cols-3">
            {daily.papers.map((p, i) => (
              <PaperCard key={p.id} paper={p} index={i + 1} whyEpistemic={llm ? "MODEL_OPINION" : "INFERENCE"} />
            ))}
          </div>
        ) : (
          <Quiet>No papers in the tracked arXiv categories matched an Anti Fund thesis area in the last 30 days.</Quiet>
        )}
      </Section>

      <Section n={4} id="d-strange" title="Three strange signals" right={<span className="tnum">{daily.strange_signals.length}</span>}>
        {daily.strange_signals.length > 0 ? (
          <div className="grid gap-x-10 gap-y-10 md:grid-cols-3">
            {daily.strange_signals.map((s, i) => (
              <StrangeSignalDispatch key={s.id} signal={s} index={i + 1} className="border-t border-ink pt-3" />
            ))}
          </div>
        ) : (
          <Quiet>No unusual patterns detected in this window.</Quiet>
        )}
      </Section>

      <Section n={5} id="d-watch" title="Watchlist changes" right={<span className="tnum">{daily.watchlist_changes.length}</span>}>
        {daily.watchlist_changes.length > 0 ? (
          <ul>
            {daily.watchlist_changes.map((w) => (
              <li key={w.entity_id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 border-t border-rule py-3.5">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <Link
                      href={hrefFor(w.entity_type, w.slug)}
                      className="font-serif text-[19px] leading-tight text-ink decoration-accent underline-offset-4 hover:underline"
                    >
                      {w.name}
                    </Link>
                    {w.is_demo && <DemoBadge />}
                  </div>
                  <ul className="mt-1.5 space-y-0.5">
                    {w.changes.map((ch, i) => (
                      <li key={i} className="grid grid-cols-[14px_1fr] text-[13px] leading-snug text-ink">
                        <span aria-hidden className="text-ink-3">→</span>
                        {ch}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="tnum font-serif text-[26px] leading-none text-ink">{w.score ?? "—"}</span>
                  <Delta value={w.delta} size="xs" />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="Nothing on your watchlist moved."
            action={
              <Link href="/watchlist" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                Open watchlist →
              </Link>
            }
          >
            Watched companies and founders report score moves, new signals and role changes here. Press W on any card to watch it.
          </EmptyState>
        )}
      </Section>

      <Section n={6} id="d-new" title="Entered the radar this week" right={<span className="tnum">{daily.new_entities.length}</span>}>
        {daily.new_entities.length > 0 ? (
          <NewEntityList entities={daily.new_entities} />
        ) : (
          <Quiet>No new companies or founders were first observed this week.</Quiet>
        )}
        <Link href="/?new=1" className="label mt-4 inline-block text-ink hover:text-accent">
          See everything Unknown Yesterday on the radar →
        </Link>
      </Section>
    </div>
  );
}
