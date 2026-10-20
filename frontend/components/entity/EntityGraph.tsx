"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { hrefFor, humanize } from "@/lib/format";
import type { GraphEdge, GraphNode, GraphOut } from "@/lib/types";

export interface EntityGraphProps {
  graph: GraphOut;
  focalId: string;
  /** Max nodes drawn (focal + highest-degree neighbours). */
  maxNodes?: number;
  className?: string;
}

interface SimNode extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  depth: number;
  degree: number;
}

const W = 960;
const H = 460;
const PAD = 34;

/** Deterministic force layout: seeded rings by BFS depth, then a fixed number of relaxation steps. */
function layout(nodes: GraphNode[], edges: GraphEdge[], focalId: string): SimNode[] {
  const adj = new Map<string, Set<string>>();
  for (const n of nodes) adj.set(n.id, new Set());
  for (const e of edges) {
    adj.get(e.source)?.add(e.target);
    adj.get(e.target)?.add(e.source);
  }
  const depth = new Map<string, number>([[focalId, 0]]);
  const queue = [focalId];
  while (queue.length) {
    const id = queue.shift()!;
    for (const nb of adj.get(id) ?? []) {
      if (!depth.has(nb)) {
        depth.set(nb, (depth.get(id) ?? 0) + 1);
        queue.push(nb);
      }
    }
  }
  const byDepth = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 3;
    byDepth.set(d, [...(byDepth.get(d) ?? []), n]);
  }
  const cx = W / 2;
  const cy = H / 2;
  const sim: SimNode[] = [];
  for (const [d, group] of byDepth) {
    group.forEach((n, i) => {
      const angle = (i / group.length) * Math.PI * 2 + d * 0.7;
      const r = d === 0 ? 0 : 70 + d * 75;
      // Round the trig seed: Math.sin/cos may differ in the last ulp between the server (Node) and the browser,
      // and the simulation would amplify that into a hydration mismatch. Everything after is exact IEEE math.
      sim.push({ ...n, x: Math.round((cx + Math.cos(angle) * r * 1.35) * 1000) / 1000, y: Math.round((cy + Math.sin(angle) * r * 0.8) * 1000) / 1000, vx: 0, vy: 0, depth: d, degree: adj.get(n.id)?.size ?? 0 });
    });
  }
  const index = new Map(sim.map((n, i) => [n.id, i]));
  const links = edges
    .map((e) => [index.get(e.source), index.get(e.target)] as const)
    .filter((l): l is readonly [number, number] => l[0] !== undefined && l[1] !== undefined);

  const ITER = 320;
  for (let t = 0; t < ITER; t++) {
    const alpha = 1 - t / ITER;
    // repulsion
    for (let i = 0; i < sim.length; i++) {
      for (let j = i + 1; j < sim.length; j++) {
        const a = sim[i];
        const b = sim[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = (i - j) * 0.1;
          dy = 0.1;
          d2 = dx * dx + dy * dy;
        }
        const f = (7000 / d2) * alpha;
        const d = Math.sqrt(d2);
        a.vx -= (dx / d) * f;
        a.vy -= (dy / d) * f;
        b.vx += (dx / d) * f;
        b.vy += (dy / d) * f;
      }
    }
    // springs
    for (const [i, j] of links) {
      const a = sim[i];
      const b = sim[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const k = ((d - 130) / d) * 0.05 * alpha;
      a.vx += dx * k;
      a.vy += dy * k;
      b.vx -= dx * k;
      b.vy -= dy * k;
    }
    for (const n of sim) {
      // gentle gravity, wider than tall to suit the frame
      n.vx += (cx - n.x) * 0.002 * alpha;
      n.vy += (cy - n.y) * 0.006 * alpha;
      if (n.id === focalId) {
        n.x = cx;
        n.y = cy;
        n.vx = n.vy = 0;
        continue;
      }
      n.x = Math.max(PAD, Math.min(W - PAD, n.x + n.vx));
      n.y = Math.max(PAD, Math.min(H - PAD, n.y + n.vy));
      n.vx *= 0.55;
      n.vy *= 0.55;
    }
  }
  // Fit the settled layout to the frame (bounded zoom) so small graphs read clearly.
  const xs = sim.map((n) => n.x);
  const ys = sim.map((n) => n.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const fx = Math.min(2.4, (W - PAD * 2 - 160) / Math.max(1, x1 - x0));
  const fy = Math.min(2.4, (H - PAD * 2) / Math.max(1, y1 - y0));
  for (const n of sim) {
    n.x = Math.round((W / 2 + (n.x - (x0 + x1) / 2) * fx) * 100) / 100;
    n.y = Math.round((H / 2 + (n.y - (y0 + y1) / 2) * fy) * 100) / 100;
  }
  return sim;
}

function trim(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function Shape({ node, focal, dim }: { node: SimNode; focal: boolean; dim: boolean }) {
  const stroke = focal ? "var(--color-accent)" : node.type === "company" || node.type === "person" ? "var(--color-ink)" : "var(--color-ink-2)";
  const fillInk = focal ? "var(--color-accent)" : "var(--color-ink)";
  const common = { stroke, strokeWidth: focal ? 1.5 : 1.15, opacity: dim ? 0.22 : 1, style: { transition: "opacity 150ms ease" } };
  switch (node.type) {
    case "company": {
      const s = focal ? 15 : 12;
      return <rect x={node.x - s / 2} y={node.y - s / 2} width={s} height={s} fill={fillInk} {...common} />;
    }
    case "person": {
      const r = focal ? 8 : 5 + Math.min(3, (node.score ?? 0) / 20);
      return <circle cx={node.x} cy={node.y} r={r} fill={focal ? fillInk : "var(--color-paper)"} {...common} strokeWidth={focal ? 1.5 : 1.5} />;
    }
    case "repository": {
      const s = 6;
      return (
        <path
          d={`M${node.x},${node.y - s}L${node.x + s},${node.y}L${node.x},${node.y + s}L${node.x - s},${node.y}Z`}
          fill={focal ? fillInk : "var(--color-paper)"}
          {...common}
        />
      );
    }
    case "paper":
      return (
        <path
          d={`M${node.x - 4.5},${node.y - 6}h6l3,3v9h-9Z`}
          fill={focal ? fillInk : "var(--color-panel)"}
          {...common}
        />
      );
    default:
      return (
        <g opacity={dim ? 0.22 : 1} style={{ transition: "opacity 150ms ease" }}>
          <circle cx={node.x} cy={node.y} r={7} fill="none" stroke="var(--color-ink-3)" strokeWidth={1} />
          <circle cx={node.x} cy={node.y} r={3} fill="var(--color-ink-3)" />
        </g>
      );
  }
}

const LEGEND: { type: string; label: string }[] = [
  { type: "person", label: "Person" },
  { type: "company", label: "Company" },
  { type: "repository", label: "Repository" },
  { type: "paper", label: "Paper" },
  { type: "org", label: "Organisation" },
];

/**
 * Small, calm relationship graph. Shapes encode node type (circle person,
 * square company, diamond repository, page paper, ring organisation); the
 * focal entity is burgundy. Hover highlights neighbours; click opens
 * person/company pages.
 */
export function EntityGraph({ graph, focalId, maxNodes = 48, className }: EntityGraphProps) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);

  const { nodes, edges, hidden } = useMemo(() => {
    const degree = new Map<string, number>();
    for (const e of graph.edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const ranked = [...graph.nodes].sort(
      (a, b) => Number(b.id === focalId) - Number(a.id === focalId) || (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) || a.label.localeCompare(b.label),
    );
    const kept = ranked.slice(0, maxNodes);
    const keep = new Set(kept.map((n) => n.id));
    const es = graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target) && e.source !== e.target);
    return { nodes: layout(kept, es, focalId), edges: es, hidden: graph.nodes.length - kept.length };
  }, [graph, focalId, maxNodes]);

  const pos = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const neighbours = useMemo(() => {
    if (!hover) return null;
    const s = new Set([hover]);
    for (const e of edges) {
      if (e.source === hover) s.add(e.target);
      if (e.target === hover) s.add(e.source);
    }
    return s;
  }, [hover, edges]);

  // Label budget: focal, companies, then highest-scored people.
  const labelled = useMemo(() => {
    const s = new Set<string>([focalId]);
    nodes.filter((n) => n.type === "company").forEach((n) => s.add(n.id));
    nodes
      .filter((n) => n.type === "person")
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      .slice(0, 10)
      .forEach((n) => s.add(n.id));
    if (nodes.length <= 24) nodes.filter((n) => n.type === "org" || n.type === "repository" || n.type === "paper").forEach((n) => s.add(n.id));
    return s;
  }, [nodes, focalId]);

  if (nodes.length <= 1) {
    return (
      <p className={clsx("border-y border-rule py-4 text-[13px] text-ink-3", className)}>
        No relationships recorded yet — the graph fills in as co-authorship, repository and affiliation links are observed.
      </p>
    );
  }

  const hovered = hover ? pos.get(hover) : null;
  const navigable = (n: GraphNode) => (n.type === "person" || n.type === "company") && !!n.slug && n.id !== focalId;
  const open = (n: GraphNode) => navigable(n) && router.push(hrefFor(n.type, n.slug!));

  return (
    <figure className={clsx("min-w-0", className)}>
      <div className="relative border-y border-rule bg-[color-mix(in_srgb,var(--color-panel)_35%,transparent)]">
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full animate-fade-in" role="img" aria-label={`Relationship graph: ${nodes.length} entities, ${edges.length} links`}>
          <g className="pointer-events-none">
            {edges.map((e, i) => {
              const a = pos.get(e.source)!;
              const b = pos.get(e.target)!;
              const lit = neighbours ? neighbours.has(e.source) && neighbours.has(e.target) && (e.source === hover || e.target === hover) : false;
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={lit ? "var(--color-ink)" : "var(--color-rule-strong)"}
                  strokeWidth={lit ? 1.2 : 0.9}
                  strokeDasharray={e.kind === "prior_affiliation" ? "3 3" : undefined}
                  opacity={neighbours && !lit ? 0.25 : 1}
                  style={{ transition: "opacity 150ms ease, stroke 150ms ease" }}
                />
              );
            })}
          </g>
          {nodes.map((n) => {
            const focal = n.id === focalId;
            const dim = !!neighbours && !neighbours.has(n.id);
            const showLabel = (labelled.has(n.id) && !dim) || (neighbours?.has(n.id) ?? false);
            const nav = navigable(n);
            const right = n.x < W - 150;
            return (
              <g
                key={n.id}
                onMouseEnter={() => setHover(n.id)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(n.id)}
                onBlur={() => setHover(null)}
                onClick={() => open(n)}
                onKeyDown={(ev) => {
                  if (ev.key === "Enter") open(n);
                }}
                tabIndex={nav ? 0 : -1}
                role={nav ? "link" : undefined}
                aria-label={`${humanize(n.type)}: ${n.label}`}
                className={clsx("outline-none", nav && "cursor-pointer")}
              >
                <circle cx={n.x} cy={n.y} r={14} fill="transparent" />
                <Shape node={n} focal={focal} dim={dim} />
                {showLabel && (
                  <text
                    x={n.x + (right ? 12 : -12)}
                    y={n.y}
                    dy="0.35em"
                    textAnchor={right ? "start" : "end"}
                    className={clsx(
                      "pointer-events-none select-none",
                      focal ? "fill-accent font-serif text-[15px]" : n.type === "company" ? "fill-ink font-serif text-[13px]" : "fill-ink-2 text-[11px]",
                    )}
                    style={{ paintOrder: "stroke", stroke: "var(--color-paper)", strokeWidth: 3, strokeLinejoin: "round" }}
                  >
                    {trim(n.type === "repository" && hover !== n.id ? n.label.split("/").pop() ?? n.label : n.label, hover === n.id ? 60 : 26)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hovered && (
          <div
            aria-hidden
            className="pointer-events-none absolute left-3 top-3 max-w-[300px] animate-fade-in border border-ink bg-paper px-2.5 py-1.5"
          >
            <p className="label text-ink-3">
              {humanize(hovered.type)}
              {hovered.score !== null && <span className="tnum ml-2 text-ink">score {hovered.score}</span>}
              {hovered.is_demo && <span className="ml-2">· demo</span>}
            </p>
            <p className="text-[12.5px] leading-snug text-ink">{hovered.label}</p>
            <p className="font-mono text-[10px] text-ink-3">
              {neighbours ? neighbours.size - 1 : 0} links{navigable(hovered) ? " · click to open" : ""}
            </p>
          </div>
        )}
      </div>
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {LEGEND.filter((l) => nodes.some((n) => n.type === l.type) || l.type === "person").map((l) => (
          <span key={l.type} className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
            <svg width={14} height={14} viewBox="0 0 14 14" aria-hidden>
              <Shape node={{ id: l.type, type: l.type, label: "", slug: null, score: null, is_demo: false, x: 7, y: 7, vx: 0, vy: 0, depth: 1, degree: 0 }} focal={false} dim={false} />
            </svg>
            {l.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-2">
          <svg width={18} height={6} aria-hidden>
            <line x1={0} x2={18} y1={3} y2={3} stroke="var(--color-ink-2)" strokeDasharray="3 3" />
          </svg>
          Prior affiliation
        </span>
        <span className="tnum ml-auto font-mono text-[10px] text-ink-3">
          {nodes.length} nodes · {edges.length} links{hidden > 0 && ` · ${hidden} more not drawn`}
        </span>
      </figcaption>
    </figure>
  );
}
