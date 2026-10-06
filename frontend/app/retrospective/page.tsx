import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { EpistemicBadge } from "@/components/Badges";
import { EvidenceList } from "@/components/EvidenceList";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { IngestRepoButton } from "@/components/insight/retro/IngestRepoButton";
import { StarsChart } from "@/components/insight/retro/StarsChart";
import { getRetrospective } from "@/lib/api";
import { fmtCompact, fmtDate, hrefFor, pad2, sourceLabel } from "@/lib/format";
import type { RetroEntry } from "@/lib/types";

export const metadata: Metadata = { title: "Retrospective" };

const NOT_INGESTED = "Not yet ingested";

function repoFromNote(note: string): string | null {
  return /--repo\s+([\w.-]+\/[\w.-]+)/.exec(note)?.[1] ?? null;
}

function historySpan(e: RetroEntry): string | null {
  const pts = e.series[0]?.points ?? [];
  if (pts.length < 2) return null;
  return `${fmtDate(pts[0].day)} → ${fmtDate(pts[pts.length - 1].day)}`;
}

/** Split the backend's "; "-joined history note into have / don't-have clauses. */
function historyClauses(note: string): { text: string; available: boolean }[] {
  return note
    .replace(/\.$/, "")
    .split(/;\s*/)
    .filter(Boolean)
    .map((t) => ({ text: t.charAt(0).toUpperCase() + t.slice(1), available: !/not captured|^no |not yet/i.test(t) }));
}

function Scorecard({ entries }: { entries: RetroEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse border-t border-ink text-[13px]">
        <caption className="sr-only">Retrospective projects at a glance</caption>
        <thead>
          <tr className="border-b border-ink">
            {["#", "Project", "Framework reading", "Families", "Stars history", "Score"].map((h, i) => (
              <th
                key={h}
                scope="col"
                className={clsx("label whitespace-nowrap px-2 py-2 text-left font-normal", i === 5 && "text-right")}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => {
            const pts = e.series[0]?.points ?? [];
            return (
              <tr key={e.name} className="border-b border-rule align-top">
                <td className="tnum w-10 px-2 py-2.5 font-mono text-[11px] text-ink-3">{pad2(i + 1)}</td>
                <td className="px-2 py-2.5">
                  <a href={`#retro-${i + 1}`} className="font-serif text-[16px] leading-tight text-ink hover:text-accent">
                    {e.name}
                  </a>
                </td>
                <td className="max-w-[440px] px-2 py-2.5 text-[12.5px] leading-snug text-ink-2">{e.framework_reading}</td>
                <td className="tnum whitespace-nowrap px-2 py-2.5 font-mono text-[11px] text-ink-2">
                  {e.signal_families.length || "—"}
                </td>
                <td className="tnum whitespace-nowrap px-2 py-2.5 font-mono text-[11px] text-ink-2">
                  {pts.length > 1 ? `${fmtCompact(pts[0].value)} → ${fmtCompact(pts[pts.length - 1].value)}` : "—"}
                </td>
                <td className="tnum px-2 py-2.5 text-right font-serif text-[20px] leading-none text-ink">
                  {e.current_score ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Entry({ e, n }: { e: RetroEntry; n: number }) {
  const ingested = !e.history_note.startsWith(NOT_INGESTED);
  const repo = ingested ? null : repoFromNote(e.history_note);
  const span = historySpan(e);
  return (
    <article id={`retro-${n}`} className="grid scroll-mt-6 grid-cols-12 gap-x-10 gap-y-6 border-t border-ink pb-12 pt-5">
      <div className="col-span-12 min-w-0 lg:col-span-5">
        <div className="flex items-center gap-3">
          <span className="tnum font-mono text-[12px] text-ink">{pad2(n)}</span>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label">
            {e.signal_families.length ? e.signal_families.map(sourceLabel).join(" · ") : "No signal families"}
          </span>
        </div>
        <div className="mt-3 flex items-start justify-between gap-6">
          <h3 className="font-serif text-[30px] leading-[1.08] tracking-[-0.01em] text-ink">
            {e.slug ? (
              <Link
                href={hrefFor("company", e.slug)}
                className="decoration-accent decoration-1 underline-offset-4 hover:underline"
              >
                {e.name}
              </Link>
            ) : (
              e.name
            )}
          </h3>
          {ingested && <ScoreNumeral value={e.current_score} size="md" label="Anti now" className="shrink-0 items-end" />}
        </div>
        <p className="mt-2 text-[14px] leading-relaxed text-ink-2">
          <span className="label mr-2 text-ink-3">Why included</span>
          {e.why_included}
        </p>

        {ingested && (
          <div className="mt-5 border-l-2 border-ink pl-4">
            <p className="label mb-1.5 flex items-center gap-2 text-ink">
              Framework reading today <EpistemicBadge value="INFERENCE" />
            </p>
            <p className="font-serif text-[18px] leading-snug text-ink">{e.framework_reading}</p>
            {e.current_score !== null && (
              <p className="mt-1.5 text-[12px] text-ink-3">
                Score of the owning organisation as of today — not what the radar would have said before the breakout.
              </p>
            )}
          </div>
        )}

        <div className="mt-5">
          <p className="label mb-2 text-ink">History available</p>
          <ul className="space-y-1">
            {historyClauses(e.history_note).map((c) => (
              <li key={c.text} className="grid grid-cols-[16px_1fr] text-[12.5px] leading-snug">
                <span aria-hidden className={c.available ? "text-ink" : "text-ink-3"}>
                  {c.available ? "●" : "○"}
                </span>
                <span className={c.available ? "text-ink" : "text-ink-3"}>{c.text}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="col-span-12 min-w-0 lg:col-span-7">
        {ingested ? (
          <>
            <div className="mb-2 flex items-baseline justify-between gap-4">
              <p className="label text-ink">Cumulative GitHub stars</p>
              {span && <p className="tnum font-mono text-[10.5px] text-ink-3">{span}</p>}
            </div>
            {e.series.length ? (
              <StarsChart
                series={e.series.slice(0, 1).map((s) => ({ label: s.label, points: s.points, source: s.source }))}
                note={
                  e.series[0].note ??
                  (e.series[0].source === "mixed"
                    ? "Reconstructed from stargazer timestamps, joined to daily snapshots since capture. Stars are attention as much as adoption."
                    : null)
                }
                ariaLabel={`${e.name}: cumulative GitHub stars over time`}
              />
            ) : (
              <p className="border-y border-rule py-10 text-center text-[13px] text-ink-3">
                No star history could be reconstructed.
              </p>
            )}
            {e.observable_signals.length > 0 && (
              <div className="mt-6">
                <p className="label mb-2.5 text-ink">Signals the radar observed</p>
                <EvidenceList items={e.observable_signals} limit={3} dense />
              </div>
            )}
          </>
        ) : (
          <div className="flex h-full min-h-[190px] flex-col items-start justify-center gap-4 border-y border-rule px-1 py-8">
            <p className="max-w-[48ch] text-[13.5px] leading-relaxed text-ink-2">
              Not ingested, so there is nothing to read — no stars history, no signals, no score. The radar does not backfill from
              memory.
            </p>
            {repo && <IngestRepoButton repo={repo} />}
          </div>
        )}
      </div>
    </article>
  );
}

export default async function RetrospectivePage() {
  const retro = await getRetrospective();
  const ingested = retro.entries.filter((e) => !e.history_note.startsWith(NOT_INGESTED));

  return (
    <div>
      <PageHeader
        kicker={
          <>
            Retrospective · {retro.entries.length} known projects · {ingested.length} ingested
          </>
        }
        title={<>How the framework reads breakouts that already happened.</>}
        dek="A sanity check on the signal framework, run on open-source projects whose success is already public. It tells us whether the signals we measure are visible on real breakouts — not whether we would have invested."
      />

      <section
        aria-labelledby="retro-disclaimer"
        className="mb-14 grid grid-cols-12 gap-x-10 gap-y-6 border-y-2 border-accent py-6"
      >
        <div className="col-span-12 lg:col-span-5">
          <p className="label mb-2 text-accent">Read this first</p>
          <h2 id="retro-disclaimer" className="font-serif text-[28px] leading-[1.1] tracking-[-0.01em] text-ink">
            Retrospective signal analysis — <em className="italic">not a backtest.</em>
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-ink">{retro.disclaimer}</p>
        </div>
        <div className="col-span-12 lg:col-span-7 lg:border-l lg:border-rule lg:pl-10">
          <p className="label mb-2 text-ink">Method</p>
          <p className="text-[13.5px] leading-relaxed text-ink-2">{retro.method}</p>
          <p className="mt-3 text-[12.5px] leading-relaxed text-ink-3">
            Survivorship bias is built in: every project here was chosen because it succeeded. The interesting question is which
            signals were visible early, not whether the score is high today.
          </p>
        </div>
      </section>

      <section className="mb-16" aria-labelledby="retro-scorecard">
        <SectionLabel index={1} id="retro-scorecard" right={`${ingested.length} of ${retro.entries.length} ingested`}>
          At a glance
        </SectionLabel>
        <div className="mt-5">
          <Scorecard entries={retro.entries} />
        </div>
      </section>

      <section className="mb-16" aria-labelledby="retro-entries">
        <SectionLabel index={2} id="retro-entries">
          Project by project
        </SectionLabel>
        <div className="mt-6">
          {retro.entries.map((e, i) => (
            <Entry key={e.name} e={e} n={i + 1} />
          ))}
        </div>
      </section>

      <section aria-labelledby="retro-takeaways">
        <SectionLabel index={3} id="retro-takeaways" right={<EpistemicBadge value="INFERENCE" />}>
          Takeaways
        </SectionLabel>
        {retro.takeaways.length ? (
          <ol className="mt-6 grid gap-x-10 gap-y-5 md:grid-cols-2">
            {retro.takeaways.map((t, i) => (
              <li key={i} className="grid grid-cols-[36px_1fr] border-t border-rule pt-3">
                <span className="tnum font-mono text-[11px] text-ink-3">{pad2(i + 1)}</span>
                <span className="font-serif text-[18px] leading-snug text-ink">{t}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-6 text-[13.5px] text-ink-2">No takeaways yet — ingest the projects above first.</p>
        )}
        <p className="mt-8 max-w-[72ch] text-[12.5px] leading-relaxed text-ink-3">
          What would change our mind: if early-stage signals (first releases, contributor growth from outside the founding team,
          papers with code) were consistently absent before these breakouts, the framework would be measuring fame, not formation.
        </p>
      </section>
    </div>
  );
}
