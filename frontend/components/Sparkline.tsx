"use client";

import clsx from "clsx";
import { useState, type MouseEvent } from "react";
import { fmtNum } from "@/lib/format";

export interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  /** Describes the series, e.g. "GitHub stars, last 30 days". */
  label?: string | null;
  /** Mark the last point in accent. */
  accentLast?: boolean;
  className?: string;
}

/** Tiny axis-free trend line with a hover readout of the hovered point. */
export function Sparkline({ values, width = 128, height = 30, label, accentLast = true, className }: SparklineProps) {
  const [hover, setHover] = useState<number | null>(null);
  const data = values.filter((v) => Number.isFinite(v));
  if (data.length < 2) {
    return (
      <span className={clsx("label text-ink-3", className)} style={{ width }}>
        Insufficient history
      </span>
    );
  }
  const pad = 2.5;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const x = (i: number) => pad + (i / (data.length - 1)) * (width - pad * 2);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);
  const d = data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = data.length - 1;
  const shownIdx = hover ?? last;

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = ((e.clientX - rect.left) / rect.width) * width;
    const i = Math.round(((rel - pad) / (width - pad * 2)) * last);
    setHover(Math.max(0, Math.min(last, i)));
  };

  const summary = `${label ?? "Trend"}: from ${fmtNum(data[0])} to ${fmtNum(data[last])}`;
  return (
    <figure className={clsx("inline-flex flex-col gap-1", className)}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={summary}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        className="overflow-visible"
      >
        <path d={d} fill="none" stroke="var(--color-ink)" strokeWidth={1.25} strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={0} y2={height} stroke="var(--color-rule-strong)" strokeWidth={1} />
        )}
        <circle
          cx={x(shownIdx)}
          cy={y(data[shownIdx])}
          r={2.5}
          fill={accentLast && hover === null ? "var(--color-accent)" : "var(--color-ink)"}
          stroke="var(--color-paper)"
          strokeWidth={1.5}
        />
      </svg>
      <figcaption className="flex items-baseline justify-between gap-2 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
        <span className="truncate">{label}</span>
        <span className="tnum text-ink-2">
          {fmtNum(data[shownIdx])}
        </span>
      </figcaption>
    </figure>
  );
}
