import clsx from "clsx";
import type { ReactNode } from "react";
import { Delta } from "@/components/Delta";
import { ScoreBar } from "@/components/ScoreBar";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SourceGlyph, SourceLink } from "@/components/SourceLink";
import { Sparkline } from "@/components/Sparkline";
import { WhyNow } from "@/components/formation/WhyNow";
import { fmtDate, fmtNum } from "@/lib/format";
import type { AntiScore, EvidenceItem, Narrative, ScorePoint, TimelineEvent, VelocityMetric } from "@/lib/types";
import { EpistemicBadge } from "@/components/Badges";
import { ageShort, attentionView } from "@/lib/labels";
import type { AttentionGap, Epistemic } from "@/lib/types";
import { attentionExplanation, type CollisionLine, type FamilyPresence, velocityBasisLabel } from "./entityLogic";
import { Quiet } from "./primitives";
import { WhyButton } from "./WhyButton";

/** Epistemic value to display for a narrative: anything a model wrote is ANALYSIS (MODEL_OPINION). */
function narrativeEpistemic(n: Narrative): Epistemic {
  return n.generator === "llm" || n.generator === "cached" ? "MODEL_OPINION" : n.epistemic;
}

/** HIGH / MEDIUM / LOW collision strength + the human sentence + family names. */
export function CollisionLineView({ line, className }: { line: CollisionLine; className?: string }) {
  return (
    <p className={clsx("flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-rule pt-3", className)}>
      <span className="label text-ink-3">Signal collision</span>
      {line.strength && (
        <span
          className={clsx(
            "font-mono text-[11px] uppercase tracking-[0.1em]",
            line.strength === "HIGH" ? "text-accent" : line.strength === "MEDIUM" ? "text-ink" : "text-ink-2",
          )}
        >
          {line.strength}
        </span>
      )}
      <span className="text-[14px] text-ink">{line.sentence}</span>
      <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">{line.families.join(" · ")}</span>
    </p>
  );
}

/**
 * WHY NOW — the lead of the page: one sentence, dated evidence bullets, the collision line.
 * The sentence is labelled INFERRED (template) or ANALYSIS (language model).
 */
export function WhyNowLead({
  narrative,
  fallback,
  evidence,
  collision,
  demo = false,
  emptyText,
}: {
  narrative: Narrative | null;
  fallback?: string[];
  evidence: EvidenceItem[];
  collision: CollisionLine | null;
  demo?: boolean;
  emptyText: string;
}) {
  const sentence = narrative?.text ?? fallback?.[0] ?? null;
  if (!sentence && evidence.length === 0) return <Quiet>{emptyText}</Quiet>;
  return (
    <div className="space-y-5">
      {sentence && (
        <div>
          <p className="max-w-[68ch] font-serif text-[22px] leading-[1.35] text-ink">{sentence}</p>
          <p className="mt-2 flex flex-wrap items-center gap-2">
            <EpistemicBadge value={narrative ? narrativeEpistemic(narrative) : "INFERENCE"} />
            {narrative?.generator === "template" && <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">Template · built from dated records</span>}
            {narrative?.model && <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{narrative.model}</span>}
          </p>
        </div>
      )}
      {!narrative && fallback && fallback.length > 1 && (
        <ul className="space-y-1.5">
          {fallback.slice(1, 4).map((w, i) => (
            <li key={i} className="grid grid-cols-[16px_1fr] text-[14px] leading-snug text-ink">
              <span aria-hidden className="text-ink-3">→</span>
              {w}
            </li>
          ))}
        </ul>
      )}
      {evidence.length > 0 && (
        <div>
          <p className="label mb-2.5 text-ink-3">
            Dated evidence
            {sentence && /^no new signals/i.test(sentence) && (
              <span className="normal-case tracking-normal"> — from linked repositories, papers and organizations, not this record&apos;s own signals</span>
            )}
          </p>
          <WhyNow items={evidence} size="lead" demo={demo} />
        </div>
      )}
      {collision && <CollisionLineView line={collision} />}
    </div>
  );
}

/** WHAT CHANGED — recent dated events plus velocity with honest "insufficient history" states. */
export function WhatChanged({
  events,
  velocity,
  scoreDelta,
  windowDays,
  demo = false,
}: {
  events: TimelineEvent[];
  velocity: VelocityMetric[];
  scoreDelta?: { d7: number | null; d30: number | null; label: string } | null;
  windowDays: number;
  demo?: boolean;
}) {
  const hasDelta = scoreDelta && (scoreDelta.d7 !== null || scoreDelta.d30 !== null);
  if (events.length === 0 && velocity.length === 0 && !hasDelta) {
    return <Quiet>No dated change in the last {windowDays} days, and no velocity could be computed — insufficient history.</Quiet>;
  }
  return (
    <div className="grid gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      <div className="min-w-0">
        <p className="label mb-2 text-ink-3">Last {windowDays} days</p>
        {events.length ? (
          <ol className="divide-y divide-rule border-y border-rule">
            {events.map((e, i) => (
              <li key={`${e.day}-${i}`} className="grid grid-cols-[52px_26px_1fr] items-baseline gap-x-2 py-2">
                <time dateTime={e.day} className="tnum font-mono text-[10.5px] uppercase text-ink-2">
                  {fmtDate(e.day, { year: false })}
                </time>
                <SourceGlyph sourceType={e.source_type} className="translate-y-[2px] self-start" />
                <span className="min-w-0 text-[13.5px] leading-snug text-ink">
                  {e.title}
                  {e.source_url && <SourceLink url={e.source_url} demo={demo || e.is_demo} className="ml-2 whitespace-nowrap" />}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="border-y border-rule py-3 text-[13px] text-ink-3">No dated events in this window.</p>
        )}
      </div>
      <div className="min-w-0">
        <p className="label mb-2 text-ink-3">Velocity</p>
        {velocity.length ? (
          <dl className="divide-y divide-rule border-y border-rule">
            {velocity.map((v) => (
              <div key={v.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 py-2" title={v.note ?? undefined}>
                <dt className="min-w-0">
                  <span className="block text-[12.5px] text-ink-2">{v.label}</span>
                  {v.note && <span className="block font-mono text-[9.5px] leading-snug text-ink-3">{v.note}</span>}
                </dt>
                <dd className="text-right">
                  <span className={clsx("tnum block font-mono text-[12.5px]", v.basis === "insufficient_history" ? "text-ink-3" : "text-ink")}>
                    {v.basis === "insufficient_history" ? "Insufficient history" : v.human.replace(/\s*\(reconstructed\)$/, "")}
                  </span>
                  {v.basis !== "insufficient_history" && (
                    <span className={clsx("font-mono text-[9px] uppercase tracking-[0.08em]", v.basis === "observed" ? "text-ink-2" : "text-ink-3")}>
                      {velocityBasisLabel(v)}
                    </span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="border-y border-rule py-3 text-[13px] text-ink-3">Velocity unavailable — insufficient observed history.</p>
        )}
        {hasDelta && (
          <p className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="label text-ink-3">{scoreDelta!.label}</span>
            {scoreDelta!.d7 !== null && <Delta value={scoreDelta!.d7} period="7d" size="xs" />}
            {scoreDelta!.d30 !== null && <Delta value={scoreDelta!.d30} period="30d" size="xs" />}
          </p>
        )}
      </div>
    </div>
  );
}

/** INDEPENDENT SIGNALS — all six source families with an honest state, plus the source drawer trigger. */
export function IndependentSignals({ families, drawer, now }: { families: FamilyPresence[]; drawer?: ReactNode; now: Date }) {
  const observed = families.filter((f) => f.state === "observed").length;
  return (
    <div>
      <p className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <span className="text-[14px] text-ink">
          <span className="tnum font-serif text-[22px]">{observed}</span> of {families.length} source families observed
          {observed < 2 && <span className="text-ink-2"> — single-source evidence is weaker than independent signals</span>}
        </span>
        {drawer}
      </p>
      <ul className="grid grid-cols-2 gap-x-6 border-t border-ink sm:grid-cols-3 lg:grid-cols-6">
        {families.map((f) => (
          <li key={f.key} className="min-w-0 border-b border-rule py-2.5">
            <p className="flex items-center gap-1.5">
              <span
                aria-hidden
                className={clsx("font-mono text-[11px]", f.state === "observed" ? "text-ink" : f.state === "unavailable" ? "text-ink-3" : "text-rule-strong")}
              >
                {f.state === "observed" ? "●" : f.state === "unavailable" ? "✕" : "○"}
              </span>
              <span className={clsx("label", f.state === "observed" ? "text-ink" : "text-ink-3")}>{f.label}</span>
            </p>
            <p className="mt-1 font-mono text-[10px] leading-snug text-ink-2">
              {f.state === "observed" ? (
                <>
                  {f.inWindow > 0 && (
                    <span className="text-ink">
                      {f.inWindow} in window
                      {f.lastObserved ? " · " : ""}
                    </span>
                  )}
                  {f.lastObserved ? `seen ${ageShort(f.lastObserved, now) ?? fmtDate(f.lastObserved)}` : ""}
                </>
              ) : (
                <span className="text-ink-3">{f.note}</span>
              )}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** ANTI SCORE, secondary: numeral, deltas, trend, component bars, Why drawer. */
export function ScoreCompact({
  score,
  value,
  label,
  history,
  entityName,
  isDemo = false,
}: {
  score: AntiScore | null;
  value: number | null;
  label: "Anti Score" | "Founder Score";
  history: ScorePoint[];
  entityName: string;
  isDemo?: boolean;
}) {
  const total = score?.total ?? value;
  if (total === null || total === undefined) return <Quiet>Not scored yet — scoring runs after the next ingestion sweep.</Quiet>;
  return (
    <div className="grid gap-x-8 gap-y-5 sm:grid-cols-[auto_minmax(0,1fr)]">
      <div className="flex min-w-[150px] flex-col gap-2">
        <ScoreNumeral value={total} size="lg" accent label={label} outOf={100} />
        <div className="flex flex-col gap-1">
          {score?.delta_30d !== null && score?.delta_30d !== undefined && <Delta value={score.delta_30d} period="this month" size="sm" />}
          <Delta value={score?.delta_7d ?? null} period="this week" size="xs" accentAt={Infinity} />
        </div>
        {history.length > 1 && <Sparkline values={history.map((p) => p.score)} width={150} height={28} label={`${label} since ${fmtDate(history[0].day, { year: false })}`} />}
        <WhyButton score={score} entityName={entityName} scoreLabel={label} isDemo={isDemo} className="mt-1 self-start" />
        {score && (
          <span className={clsx("tnum font-mono text-[9.5px] uppercase tracking-[0.06em]", score.low_coverage ? "text-accent" : "text-ink-3")}>
            {score.low_coverage ? "Low coverage · " : ""}v{score.version} · n={score.cohort_size}
          </span>
        )}
      </div>
      {score?.components.length ? (
        <ul className="space-y-2.5 self-start">
          {score.components.map((c) => (
            <li key={c.key}>
              <ScoreBar value={c.value} max={c.max} label={c.label} showValue />
            </li>
          ))}
        </ul>
      ) : (
        <Quiet>No component breakdown recorded.</Quiet>
      )}
    </div>
  );
}

function Figure({ label, value, sub, muted }: { label: string; value: string; sub?: string; muted?: boolean }) {
  return (
    <div className="min-w-0 border-t border-ink pt-2">
      <p className="label">{label}</p>
      <p className={clsx("tnum mt-1 font-serif text-[30px] leading-none", muted ? "text-ink-3" : "text-ink")}>{value}</p>
      {sub && <p className="mt-1 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">{sub}</p>}
    </div>
  );
}

/** Attention Gap 2.0: TECHNICAL MOMENTUM / PUBLIC ATTENTION / GAP + why + per-source availability. */
export function AttentionGap2Block({ gap }: { gap: AttentionGap | null }) {
  const v = gap ? attentionView(gap) : null;
  if (!v || !gap) return <Quiet>Attention gap not computed — technical or attention inputs are missing for this entity.</Quiet>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Figure label="Technical momentum" value={fmtNum(v.technical, 0)} sub="cohort percentile" />
        <Figure label="Public attention" value={v.attention === null ? "—" : fmtNum(v.attention, 0)} sub={v.attention === null ? "not measured" : "cohort percentile"} muted={v.attention === null} />
        <Figure
          label="Gap"
          value={v.gap === null ? "—" : `${v.gap > 0 ? "+" : ""}${fmtNum(v.gap, 0)}`}
          sub={v.gap === null ? "no gap claimed" : gap.flagged ? "flagged" : `${gap.coverage} coverage`}
          muted={v.gap === null}
        />
      </div>
      <p className="max-w-[68ch] text-[13.5px] leading-relaxed text-ink">{attentionExplanation(v)}</p>
      <ul className="space-y-1 border-t border-rule pt-2">
        {(gap.sources ?? []).map((s) => (
          <li key={s.key} className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-baseline gap-2 font-mono text-[10.5px]">
            <span aria-hidden className={s.available ? "text-ink" : "text-ink-3"}>
              {s.available ? "●" : "✕"}
            </span>
            <span className={s.available ? "text-ink-2" : "text-ink-3"}>{s.available ? s.label : s.note ?? `${s.label}: unavailable`}</span>
            <span className="tnum text-ink-3">{s.available && s.percentile !== null && s.percentile !== undefined ? `p${Math.round(s.percentile)}` : ""}</span>
          </li>
        ))}
        {!v.socialAvailable && !(gap.sources ?? []).some((s) => /^x_|social/i.test(s.key)) && (
          <li className="grid grid-cols-[14px_1fr] gap-2 font-mono text-[10.5px] text-ink-3">
            <span aria-hidden>✕</span>Social attention unavailable — X API access not configured
          </li>
        )}
      </ul>
    </div>
  );
}
