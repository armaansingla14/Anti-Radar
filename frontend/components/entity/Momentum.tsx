import clsx from "clsx";
import { BarSeries } from "@/components/BarSeries";
import { LineChart, type ChartMarker } from "@/components/LineChart";
import { fmtCompact, fmtDate, fmtSigned } from "@/lib/format";
import type { TimeSeries } from "@/lib/types";
import { Quiet } from "./primitives";

const SOURCE_COPY: Record<TimeSeries["source"], { text: string; title: string }> = {
  observed: { text: "Observed", title: "Every point was observed directly by a collector" },
  backfill: { text: "Backfill", title: "Reconstructed from history (e.g. stargazer timestamps), not observed live" },
  mixed: { text: "Backfill + observed", title: "Older points are reconstructed; recent points observed live" },
  demo: { text: "Demo", title: "Synthetic series for a fictional demo entity" },
};

function ProvenanceChip({ series }: { series: TimeSeries }) {
  const meta = SOURCE_COPY[series.source] ?? SOURCE_COPY.observed;
  return (
    <span
      className={clsx(
        "inline-flex items-center px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em]",
        series.source === "demo" ? "hatch text-ink-2" : series.source === "observed" ? "border border-ink/70 text-ink" : "border border-rule-strong text-ink-2",
      )}
      title={meta.title}
    >
      {meta.text}
    </span>
  );
}

function change(series: TimeSeries): number | null {
  const p = series.points;
  if (p.length < 2) return null;
  return p[p.length - 1].value - p[0].value;
}

function Panel({ series, children, wide }: { series: TimeSeries; children: React.ReactNode; wide?: boolean }) {
  const last = series.points.at(-1);
  const delta = change(series);
  return (
    <figure className={clsx("min-w-0 border-t border-ink pt-3", wide && "md:col-span-2")}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div>
          <figcaption className="label text-ink">{series.label}</figcaption>
          <p className="mt-1 flex items-baseline gap-3">
            <span className="tnum font-serif text-[30px] leading-none text-ink">{last ? fmtCompact(last.value) : "—"}</span>
            {delta !== null && series.metric !== "commits_week" && (
              <span className="tnum font-mono text-[11px] text-ink-2">
                {fmtSigned(delta)} since {fmtDate(series.points[0].day, { year: false })}
              </span>
            )}
          </p>
        </div>
        <ProvenanceChip series={series} />
      </div>
      {children}
      {series.note && <p className="mt-1.5 font-mono text-[10px] leading-snug text-ink-3">{series.note}</p>}
    </figure>
  );
}

/** Stars / contributors line charts and weekly-commit bars, each labelled with its provenance. */
export function Momentum({ series, markers = [] }: { series: TimeSeries[]; markers?: ChartMarker[] }) {
  const by = new Map(series.map((s) => [s.metric, s]));
  const stars = by.get("stars");
  const commits = by.get("commits_week");
  const contributors = by.get("contributors");
  const forks = by.get("forks");
  if (!stars && !commits && !contributors && !forks) {
    return <Quiet>No time series yet. Momentum charts appear once a linked repository has been observed at least twice or backfilled.</Quiet>;
  }
  const small = [contributors, forks].filter((s): s is TimeSeries => !!s);
  return (
    <div className="grid gap-x-10 gap-y-8 md:grid-cols-2">
      {stars && (
        <Panel series={stars} wide>
          <LineChart
            series={[{ label: stars.label, points: stars.points, source: stars.source === "mixed" ? "observed" : stars.source }]}
            markers={markers}
            height={220}
            ariaLabel={`${stars.label} over time`}
          />
        </Panel>
      )}
      {commits && (
        <Panel series={commits}>
          <BarSeries
            data={commits.points.map((p) => ({ label: fmtDate(p.day, { year: false }), value: p.value, detail: `week of ${fmtDate(p.day)}` }))}
            highlightLast
            height={150}
            ariaLabel="Commits per week"
          />
        </Panel>
      )}
      {small.map((s) => (
        <Panel key={s.metric} series={s}>
          {s.points.length >= 2 ? (
            <LineChart series={[{ label: s.label, points: s.points, source: s.source === "mixed" ? "observed" : s.source }]} height={150} />
          ) : (
            <p className="flex h-[150px] items-center border-y border-rule text-[12.5px] text-ink-3">
              Single observation on {fmtDate(s.points[0]?.day)} — a trend needs at least two.
            </p>
          )}
        </Panel>
      ))}
    </div>
  );
}
