"use client";

import { useEffect, useState } from "react";
import { getCompany, getFounder } from "@/lib/api";
import { fmtDate, fmtNum, fmtSigned, humanize } from "@/lib/format";
import type { AntiScore, ScoreComponent } from "@/lib/types";
import { AttentionGapPlot } from "./AttentionGapPlot";
import { CollisionStrip } from "./CollisionStrip";
import { EvidenceList } from "./EvidenceList";
import { ScoreBar } from "./ScoreBar";
import { SectionLabel } from "./SectionLabel";
import { Sheet } from "./Sheet";
import { Skeleton } from "./Skeleton";

export interface WhyDrawerProps {
  open: boolean;
  onClose: () => void;
  score: AntiScore | null;
  entityName?: string;
  /** "Anti Score" for companies, "Founder Score" for people. */
  scoreLabel?: string;
  loading?: boolean;
  error?: string | null;
  /** Fictional entity: evidence links never point at real hosts. */
  isDemo?: boolean;
}

function rawValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : fmtNum(v, 3);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function ComponentBlock({ c, n, demo }: { c: ScoreComponent; n: number; demo: boolean }) {
  const raw = Object.entries(c.raw ?? {});
  return (
    <section className="border-t border-rule py-5 first:border-t-0 first:pt-0" aria-label={c.label}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-[14px] text-ink">
          <span className="tnum mr-2 font-mono text-[10.5px] text-ink-3">{String(n).padStart(2, "0")}</span>
          {c.label}
        </h3>
        <span className="tnum font-mono text-[12px] text-ink">
          {fmtNum(c.value, 1)} <span className="text-ink-3">/ {fmtNum(c.max, 0)}</span>
        </span>
      </div>
      <ScoreBar value={c.value} max={c.max} />
      <p className="mt-2.5 text-[13px] leading-relaxed text-ink-2">{c.explanation}</p>
      {raw.length > 0 && (
        <details className="group mt-3">
          <summary className="label cursor-pointer list-none text-ink-2 hover:text-ink">
            <span className="inline-block transition-transform group-open:rotate-90">→</span> Raw inputs ({raw.length})
          </summary>
          <table className="mt-2 w-full text-[12px]">
            <tbody>
              {raw.map(([k, v]) => (
                <tr key={k} className="border-b border-rule last:border-0">
                  <th scope="row" className="py-1 pr-3 text-left font-mono text-[10.5px] font-normal uppercase tracking-[0.04em] text-ink-3">
                    {humanize(k)}
                  </th>
                  <td className="tnum break-all py-1 text-right font-mono text-[11.5px] text-ink">{rawValue(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {c.evidence.length > 0 && <EvidenceList items={c.evidence} limit={4} dense className="mt-3" demo={demo} />}
    </section>
  );
}

/** "Why 87?" — the full, deterministic decomposition of a score. */
export function WhyDrawer({ open, onClose, score, entityName, scoreLabel = "Anti Score", loading, error, isDemo = false }: WhyDrawerProps) {
  const title = score ? `Why ${score.total}?` : "Why this score?";
  const sum = score?.components.reduce((s, c) => s + c.value, 0) ?? 0;
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={title}
      header={
        <>
          <p className="label mb-1">
            {scoreLabel}
            {entityName && <> · {entityName}</>}
          </p>
          <h2 className="font-serif text-[40px] leading-none tracking-[-0.02em]">
            Why <span className="tnum text-accent">{score?.total ?? "—"}</span>?
          </h2>
        </>
      }
    >
      {loading && (
        <div className="space-y-6">
          <Skeleton lines={3} />
          <Skeleton lines={3} />
          <Skeleton lines={3} />
        </div>
      )}
      {!loading && error && <p className="text-[13.5px] text-accent">{error}</p>}
      {!loading && !error && !score && (
        <p className="text-[13.5px] text-ink-2">This entity has not been scored yet — there are not enough observations.</p>
      )}
      {!loading && score && (
        <div className="space-y-8">
          {score.low_coverage && (
            <p className="border-l-2 border-accent pl-3 text-[13px] text-ink">
              Low coverage: some inputs could not be measured, so this score rests on fewer sources than usual.
            </p>
          )}
          <div>
            <SectionLabel as="h3" right={<span className="tnum">Σ {fmtNum(sum, 1)}</span>}>
              Components
            </SectionLabel>
            <div className="mt-4">
              {score.components.map((c, i) => (
                <ComponentBlock key={c.key} c={c} n={i + 1} demo={isDemo} />
              ))}
            </div>
            <p className="tnum mt-1 border-t border-ink pt-2 font-mono text-[11px] text-ink-2">
              Components {fmtNum(sum, 1)}
              {score.feedback_adjustment !== 0 && <> · feedback adjustment {fmtSigned(score.feedback_adjustment, 1)}</>} · total{" "}
              <span className="text-ink">{score.total}</span>
            </p>
          </div>

          {score.attention_gap && (
            <div>
              <SectionLabel as="h3">Attention gap</SectionLabel>
              <AttentionGapPlot gap={score.attention_gap} className="mt-4" />
            </div>
          )}

          {score.collision && (
            <div>
              <SectionLabel as="h3">Signal collision</SectionLabel>
              <CollisionStrip collision={score.collision} asOf={score.computed_on} className="mt-4" />
              {score.collision.evidence.length > 0 && <EvidenceList items={score.collision.evidence} limit={6} dense className="mt-4" demo={isDemo} />}
            </div>
          )}

          <p className="border-t border-rule pt-3 font-mono text-[10px] leading-relaxed text-ink-3">
            Score version {score.version} · computed {fmtDate(score.computed_on)} · cohort n={score.cohort_size}
            {score.delta_30d !== null && <> · 30d change {fmtSigned(score.delta_30d, 1)}</>}. Percentile components are relative to
            the scored cohort on that day. Every component is computed deterministically from the listed inputs.
          </p>
        </div>
      )}
    </Sheet>
  );
}

export interface WhyDrawerLoaderProps {
  open: boolean;
  onClose: () => void;
  entityType: "company" | "person";
  /** Slug or id accepted by /api/companies/{x} or /api/founders/{x}. */
  idOrSlug: string;
  entityName?: string;
  isDemo?: boolean;
}

/** WhyDrawer that fetches the score breakdown on first open. */
export function WhyDrawerLoader({ open, onClose, entityType, idOrSlug, entityName, isDemo }: WhyDrawerLoaderProps) {
  const key = `${entityType}:${idOrSlug}`;
  const [result, setResult] = useState<{ key: string; score: AntiScore | null; error: string | null } | null>(null);

  useEffect(() => {
    if (!open || result?.key === key) return;
    let cancelled = false;
    const load =
      entityType === "company"
        ? getCompany(idOrSlug).then((d) => d.anti_score)
        : getFounder(idOrSlug).then((d) => d.founder_score);
    load
      .then((score) => !cancelled && setResult({ key, score, error: null }))
      .catch(() => !cancelled && setResult({ key, score: null, error: "Could not load the score breakdown." }));
    return () => {
      cancelled = true;
    };
  }, [open, key, entityType, idOrSlug, result?.key]);

  const ready = result?.key === key;
  return (
    <WhyDrawer
      open={open}
      onClose={onClose}
      score={ready ? result.score : null}
      error={ready ? result.error : null}
      loading={!ready}
      entityName={entityName}
      isDemo={isDemo}
      scoreLabel={entityType === "person" ? "Founder Score" : "Anti Score"}
    />
  );
}
