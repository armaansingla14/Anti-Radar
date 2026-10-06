"use client";

import clsx from "clsx";
import { useState } from "react";
import { fmtNum } from "@/lib/format";
import { niceDomain } from "@/lib/scale";
import { useMeasure } from "@/lib/useMeasure";

export interface BarDatum {
  label: string;
  value: number;
  /** Optional longer text for the hover readout. */
  detail?: string;
}

export interface BarSeriesProps {
  data: BarDatum[];
  height?: number;
  formatValue?: (v: number) => string;
  /** Accent the final bar (e.g. "this week"). */
  highlightLast?: boolean;
  className?: string;
  ariaLabel?: string;
}

/** Vertical bar series with a quiet axis, mono labels and per-bar hover readout. */
export function BarSeries({
  data,
  height = 140,
  formatValue = (v) => fmtNum(v, 0),
  highlightLast,
  className,
  ariaLabel,
}: BarSeriesProps) {
  const [ref, width] = useMeasure<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (data.length === 0) {
    return <div className={clsx("py-6 text-center text-[13px] text-ink-3", className)}>No observations.</div>;
  }
  const top = 12;
  const bottom = 20;
  const left = 34;
  const max = Math.max(0, ...data.map((d) => d.value));
  const { domain, ticks } = niceDomain(0, max || 1, 3);
  const innerW = Math.max(10, width - left);
  const innerH = height - top - bottom;
  const band = innerW / data.length;
  const barW = Math.max(2, Math.min(28, band - 2));
  const y = (v: number) => top + innerH - (v / domain[1]) * innerH;
  const labelEvery = Math.ceil(data.length / Math.max(1, Math.floor(innerW / 48)));

  return (
    <div ref={ref} className={clsx("relative w-full", className)}>
      <svg width={width} height={height} role="img" aria-label={ariaLabel ?? "Bar series"} className="block">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={width} y1={y(t)} y2={y(t)} stroke="var(--color-rule)" />
            <text x={left - 6} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 font-mono text-[9.5px] tnum">
              {formatValue(t)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = left + band * i + band / 2;
          const h = Math.max(0, y(0) - y(Math.max(0, d.value)));
          const isLast = i === data.length - 1;
          const fill = hover === i ? "var(--color-ink)" : highlightLast && isLast ? "var(--color-accent)" : "var(--color-ink-2)";
          return (
            <g key={`${d.label}-${i}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={left + band * i} y={top} width={band} height={innerH} fill="transparent" />
              <rect x={cx - barW / 2} y={y(0) - h} width={barW} height={h} fill={fill} />
              {(i % labelEvery === 0 || isLast) && (
                <text x={cx} y={height - 5} textAnchor="middle" className="fill-ink-3 font-mono text-[9px] uppercase">
                  {d.label}
                </text>
              )}
              <title>{`${d.label}: ${formatValue(d.value)}${d.detail ? ` — ${d.detail}` : ""}`}</title>
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div
          aria-hidden
          className="pointer-events-none absolute border border-ink bg-paper px-2 py-1 font-mono text-[10px] text-ink"
          style={{ left: Math.min(left + band * hover + band, width - 140), top: 0 }}
        >
          <span className="text-ink-3 uppercase">{data[hover].label}</span>{" "}
          <span className="tnum">{formatValue(data[hover].value)}</span>
          {data[hover].detail && <div className="text-ink-2">{data[hover].detail}</div>}
        </div>
      )}
    </div>
  );
}
