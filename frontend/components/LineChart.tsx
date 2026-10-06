"use client";

import clsx from "clsx";
import { useState, type MouseEvent } from "react";
import { fmtDate, fmtNum, parseDate } from "@/lib/format";
import { linear, niceDomain } from "@/lib/scale";
import { useMeasure } from "@/lib/useMeasure";

export interface LineSeries {
  label: string;
  points: { day: string; value: number }[];
  /** "backfill"/"demo" series draw dashed to signal lower provenance. */
  source?: "observed" | "backfill" | "demo" | "mixed";
}

export interface ChartMarker {
  day: string;
  label: string;
}

export interface LineChartProps {
  series: LineSeries[];
  height?: number;
  formatValue?: (v: number) => string;
  /** Vertical annotations (e.g. "Repo created"). */
  markers?: ChartMarker[];
  /** Start the y-axis at zero (default true for counts). */
  zeroBased?: boolean;
  note?: string | null;
  className?: string;
  ariaLabel?: string;
}

const STROKES = [
  { stroke: "var(--color-ink)", dash: undefined, width: 1.5 },
  { stroke: "var(--color-accent)", dash: undefined, width: 1.5 },
  { stroke: "var(--color-ink-2)", dash: "4 3", width: 1.25 },
  { stroke: "var(--color-ink-3)", dash: "1 3", width: 1.25 },
];

const M = { top: 14, right: 12, bottom: 24, left: 40 };

/** Axis-light time-series chart with crosshair readout. Max four series; one y-axis. */
export function LineChart({
  series,
  height = 200,
  formatValue = (v) => fmtNum(v, 1),
  markers = [],
  zeroBased = true,
  note,
  className,
  ariaLabel,
}: LineChartProps) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hoverT, setHoverT] = useState<number | null>(null);

  const shown = series.slice(0, STROKES.length).map((s) => ({
    ...s,
    pts: s.points
      .map((p) => ({ t: parseDate(p.day)?.getTime() ?? NaN, v: p.value, day: p.day }))
      .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
      .sort((a, b) => a.t - b.t),
  }));
  const all = shown.flatMap((s) => s.pts);
  if (all.length < 2) {
    return (
      <div className={clsx("flex items-center justify-center border-y border-rule text-[13px] text-ink-3", className)} style={{ height }}>
        Not enough observations to chart yet.
      </div>
    );
  }

  const tMin = Math.min(...all.map((p) => p.t));
  const tMax = Math.max(...all.map((p) => p.t));
  const vMin = Math.min(...all.map((p) => p.v));
  const vMax = Math.max(...all.map((p) => p.v));
  const { domain, ticks } = niceDomain(zeroBased ? Math.min(0, vMin) : vMin, vMax, 4);
  const innerW = Math.max(10, width - M.left - M.right);
  const x = linear([tMin, tMax], [M.left, M.left + innerW]);
  const y = linear(domain, [height - M.bottom, M.top]);
  const xTicks = [tMin, tMin + (tMax - tMin) / 2, tMax];

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const t = tMin + ((px - M.left) / innerW) * (tMax - tMin);
    setHoverT(Math.max(tMin, Math.min(tMax, t)));
  };

  const nearest = (pts: { t: number; v: number; day: string }[], t: number) =>
    pts.reduce((best, p) => (Math.abs(p.t - t) < Math.abs(best.t - t) ? p : best), pts[0]);

  const readout =
    hoverT === null
      ? null
      : shown.filter((s) => s.pts.length).map((s) => ({ label: s.label, p: nearest(s.pts, hoverT) }));
  const snapT = readout?.[0]?.p.t ?? null;

  return (
    <figure className={clsx("w-full", className)}>
      {shown.length > 1 && (
        <figcaption className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          {shown.map((s, i) => (
            <span key={s.label} className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
              <svg width={18} height={6} aria-hidden>
                <line x1={0} x2={18} y1={3} y2={3} stroke={STROKES[i].stroke} strokeWidth={1.5} strokeDasharray={STROKES[i].dash} />
              </svg>
              {s.label}
              {s.source && s.source !== "observed" && <span className="text-ink-3">({s.source})</span>}
            </span>
          ))}
        </figcaption>
      )}
      <div ref={ref} className="relative w-full">
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel ?? `${shown.map((s) => s.label).join(", ")} from ${fmtDate(new Date(tMin))} to ${fmtDate(new Date(tMax))}`}
          onMouseMove={onMove}
          onMouseLeave={() => setHoverT(null)}
          className="block"
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={M.left + innerW} y1={y(t)} y2={y(t)} stroke="var(--color-rule)" strokeWidth={1} />
              <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 font-mono text-[9.5px] tnum">
                {formatValue(t)}
              </text>
            </g>
          ))}
          {xTicks.map((t, i) => (
            <text
              key={i}
              x={x(t)}
              y={height - 6}
              textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
              className="fill-ink-3 font-mono text-[9.5px] uppercase tnum"
            >
              {fmtDate(new Date(t), { year: i !== 1 })}
            </text>
          ))}
          {markers.map((m) => {
            const t = parseDate(m.day)?.getTime();
            if (t === undefined || t < tMin || t > tMax) return null;
            return (
              <g key={`${m.day}-${m.label}`}>
                <line x1={x(t)} x2={x(t)} y1={M.top} y2={height - M.bottom} stroke="var(--color-ink-3)" strokeDasharray="2 3" />
                <text x={x(t) + 4} y={M.top + 2} dy="0.7em" className="fill-ink-2 font-mono text-[9px] uppercase">
                  {m.label}
                </text>
              </g>
            );
          })}
          {shown.map((s, i) => {
            if (s.pts.length < 2) return null;
            const d = s.pts.map((p, j) => `${j ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
            const dashed = s.source === "backfill" || s.source === "demo";
            return (
              <path
                key={s.label}
                d={d}
                fill="none"
                stroke={STROKES[i].stroke}
                strokeWidth={STROKES[i].width}
                strokeDasharray={STROKES[i].dash ?? (dashed ? "5 3" : undefined)}
                strokeLinejoin="round"
              />
            );
          })}
          {snapT !== null && (
            <line x1={x(snapT)} x2={x(snapT)} y1={M.top} y2={height - M.bottom} stroke="var(--color-ink-2)" strokeWidth={1} />
          )}
          {readout?.map((r, i) => (
            <circle key={r.label} cx={x(r.p.t)} cy={y(r.p.v)} r={3} fill={STROKES[i].stroke} stroke="var(--color-paper)" strokeWidth={2} />
          ))}
          <rect x={M.left} y={0} width={innerW} height={height} fill="transparent" />
        </svg>
        {readout && snapT !== null && (
          <div
            className="pointer-events-none absolute top-0 border border-ink bg-paper px-2 py-1 font-mono text-[10px] leading-snug text-ink"
            style={{ left: Math.min(Math.max(x(snapT) + 8, 0), width - 150), minWidth: 120 }}
            aria-hidden
          >
            <div className="text-ink-3 uppercase">{fmtDate(new Date(snapT))}</div>
            {readout.map((r) => (
              <div key={r.label} className="flex justify-between gap-3 tnum">
                <span className="truncate text-ink-2">{r.label}</span>
                <span>{formatValue(r.p.v)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {note && <p className="mt-1.5 font-mono text-[10px] text-ink-3">{note}</p>}
    </figure>
  );
}
