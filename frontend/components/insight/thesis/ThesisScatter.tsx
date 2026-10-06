"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { fmtNum } from "@/lib/format";
import { linear } from "@/lib/scale";
import { useMeasure } from "@/lib/useMeasure";

export interface ScatterPoint {
  key: string;
  label: string;
  theme: string;
  momentum: number;
  attention: number;
  companies: number;
  gap: boolean;
}

export interface ThesisScatterProps {
  points: ScatterPoint[];
  gapMomentum: number;
  gapAttention: number;
  height?: number;
  className?: string;
}

const M = { top: 18, right: 18, bottom: 38, left: 46 };
const CHAR_W = 5.9;
const LINE_H = 13;

interface Placed {
  p: ScatterPoint;
  cx: number;
  cy: number;
  r: number;
  lx: number;
  ly: number;
  anchor: "start" | "end";
  w: number;
  labelled: boolean;
}

function overlaps(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * Momentum (x) vs. investor attention (y) for every subcategory with linked
 * companies. The lower-right quadrant — momentum ahead of attention — uses the
 * backend's attention-gap thresholds and is the only shaded region.
 */
export function ThesisScatter({ points, gapMomentum, gapAttention, height = 400, className }: ThesisScatterProps) {
  const [ref, width] = useMeasure<HTMLDivElement>(760);
  const [hover, setHover] = useState<string | null>(null);

  const innerW = Math.max(100, width - M.left - M.right);
  const innerH = height - M.top - M.bottom;
  const { x, y } = useMemo(
    () => ({ x: linear([0, 100], [M.left, M.left + innerW]), y: linear([0, 100], [M.top + innerH, M.top]) }),
    [innerW, innerH],
  );

  const placed = useMemo<Placed[]>(() => {
    const rOf = (n: number) => 3 + Math.sqrt(Math.max(0, n)) * 1.6;
    // Label the most interesting points first so they win the space.
    const ordered = [...points].sort(
      (a, b) =>
        Number(b.gap) - Number(a.gap) || b.momentum - b.attention - (a.momentum - a.attention) || b.companies - a.companies,
    );
    const boxes: { x: number; y: number; w: number; h: number }[] = [];
    // reserve every dot
    for (const p of points) {
      const r = rOf(p.companies);
      boxes.push({ x: x(p.momentum) - r, y: y(p.attention) - r, w: r * 2, h: r * 2 });
    }
    const out: Placed[] = [];
    const offsets = [0, -1, 1, -2, 2];
    for (const p of ordered) {
      const cx = x(p.momentum);
      const cy = y(p.attention);
      const r = rOf(p.companies);
      const w = p.label.length * CHAR_W;
      let chosen: Placed | null = null;
      const notable = p.gap || p.companies >= 3 || p.momentum >= 50 || p.attention >= 60;
      if (notable)
        for (const side of ["start", "end"] as const) {
          for (const o of offsets) {
            const ly = cy + o * LINE_H;
            const lx = side === "start" ? cx + r + 6 : cx - r - 6;
            const box = { x: side === "start" ? lx : lx - w, y: ly - LINE_H / 2, w, h: LINE_H - 1 };
            if (box.x < M.left || box.x + box.w > M.left + innerW + M.right - 2) continue;
            if (box.y < 2 || box.y + box.h > M.top + innerH) continue;
            if (boxes.some((b) => overlaps(b, box))) continue;
            boxes.push(box);
            chosen = { p, cx, cy, r, lx, ly, anchor: side, w, labelled: true };
            break;
          }
          if (chosen) break;
        }
      out.push(chosen ?? { p, cx, cy, r, lx: cx, ly: cy, anchor: "start", w, labelled: false });
    }
    return out;
  }, [points, x, y, innerW, innerH]);

  const hovered = placed.find((d) => d.p.key === hover) ?? null;
  const ticks = [0, 20, 40, 60, 80, 100];

  return (
    <figure className={clsx("w-full", className)}>
      <div ref={ref} className="relative w-full">
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Scatter of ${points.length} thesis subcategories: momentum versus investor attention. ${points.filter((p) => p.gap).length} in the momentum-ahead-of-attention quadrant.`}
          className="block"
        >
          {/* quadrant: momentum ahead of attention */}
          <rect
            x={x(gapMomentum)}
            y={y(gapAttention)}
            width={x(100) - x(gapMomentum)}
            height={y(0) - y(gapAttention)}
            fill="var(--color-accent-wash)"
          />
          <text
            x={x(100) - 6}
            y={y(0) - 8}
            textAnchor="end"
            className="fill-accent font-mono text-[9.5px] uppercase tracking-[0.08em]"
          >
            Momentum ahead of attention
          </text>
          <text x={x(0) + 6} y={y(100) + 12} className="fill-ink-3 font-mono text-[9.5px] uppercase tracking-[0.08em]">
            Crowded · attention ahead
          </text>

          {ticks.map((t) => (
            <g key={`g-${t}`}>
              <line x1={x(0)} x2={x(100)} y1={y(t)} y2={y(t)} stroke="var(--color-rule)" strokeWidth={t === 0 ? 1 : 0.6} />
              <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(100)} stroke="var(--color-rule)" strokeWidth={t === 0 ? 1 : 0.6} />
              <text x={x(0) - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tnum fill-ink-3 font-mono text-[9.5px]">
                {t}
              </text>
              <text x={x(t)} y={y(0) + 14} textAnchor="middle" className="tnum fill-ink-3 font-mono text-[9.5px]">
                {t}
              </text>
            </g>
          ))}
          {/* thresholds */}
          <line x1={x(gapMomentum)} x2={x(gapMomentum)} y1={y(0)} y2={y(100)} stroke="var(--color-ink-3)" strokeDasharray="2 3" />
          <line
            x1={x(0)}
            x2={x(100)}
            y1={y(gapAttention)}
            y2={y(gapAttention)}
            stroke="var(--color-ink-3)"
            strokeDasharray="2 3"
          />

          <text
            x={x(50)}
            y={height - 4}
            textAnchor="middle"
            className="fill-ink-2 font-mono text-[9.5px] uppercase tracking-[0.1em]"
          >
            Technical momentum →
          </text>
          <text
            transform={`translate(11 ${y(50)}) rotate(-90)`}
            textAnchor="middle"
            className="fill-ink-2 font-mono text-[9.5px] uppercase tracking-[0.1em]"
          >
            Investor attention (pctl) →
          </text>

          {/* leader lines + labels */}
          {placed
            .filter((d) => d.labelled)
            .map((d) => {
              const active = hover === null || hover === d.p.key;
              const edgeX = d.anchor === "start" ? d.lx - 2 : d.lx + 2;
              const needsLeader = Math.abs(d.ly - d.cy) > 2;
              return (
                <g key={`l-${d.p.key}`} className="transition-opacity duration-150" opacity={active ? 1 : 0.25}>
                  {needsLeader && (
                    <line
                      x1={d.cx + (d.anchor === "start" ? d.r : -d.r)}
                      y1={d.cy}
                      x2={edgeX}
                      y2={d.ly}
                      stroke="var(--color-rule-strong)"
                      strokeWidth={0.75}
                    />
                  )}
                  <text
                    x={d.lx}
                    y={d.ly}
                    dy="0.34em"
                    textAnchor={d.anchor}
                    className={clsx("text-[10.5px]", d.p.gap ? "fill-accent" : "fill-ink-2")}
                  >
                    {d.p.label}
                  </text>
                </g>
              );
            })}

          {/* dots (drawn smallest last so they stay clickable) */}
          {[...placed]
            .sort((a, b) => b.r - a.r)
            .map((d) => (
              <a
                key={d.p.key}
                href={`#cell-${d.p.key}`}
                onMouseEnter={() => setHover(d.p.key)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(d.p.key)}
                onBlur={() => setHover(null)}
                aria-label={`${d.p.label}: momentum ${fmtNum(d.p.momentum, 0)}, attention ${fmtNum(d.p.attention, 0)}`}
              >
                <circle cx={d.cx} cy={d.cy} r={d.r + 5} fill="transparent" />
                <circle
                  cx={d.cx}
                  cy={d.cy}
                  r={d.r}
                  fill={d.p.gap ? "var(--color-accent)" : hover === d.p.key ? "var(--color-ink)" : "var(--color-paper)"}
                  fillOpacity={d.p.gap ? 0.9 : 1}
                  stroke={d.p.gap ? "var(--color-accent)" : "var(--color-ink)"}
                  strokeWidth={1.1}
                />
              </a>
            ))}
        </svg>

        {hovered && (
          <div
            className="pointer-events-none absolute z-10 min-w-[180px] animate-fade-in border border-ink bg-paper px-2.5 py-2 font-mono text-[10.5px] leading-snug text-ink"
            style={{
              left: Math.min(hovered.cx + 12, width - 196),
              top: Math.max(0, hovered.cy - 70),
            }}
            aria-hidden
          >
            <div className="uppercase tracking-[0.06em] text-ink-3">{hovered.p.theme}</div>
            <div className="mb-1 font-sans text-[12.5px] text-ink">{hovered.p.label}</div>
            <div className="tnum flex justify-between gap-4">
              <span className="text-ink-2">Momentum</span>
              {fmtNum(hovered.p.momentum, 1)}
            </div>
            <div className="tnum flex justify-between gap-4">
              <span className="text-ink-2">Attention</span>p{fmtNum(hovered.p.attention, 0)}
            </div>
            <div className="tnum flex justify-between gap-4">
              <span className="text-ink-2">Companies</span>
              {hovered.p.companies}
            </div>
            {hovered.p.gap && <div className="mt-1 uppercase tracking-[0.06em] text-accent">◆ Possible attention gap</div>}
          </div>
        )}
      </div>
    </figure>
  );
}
