import clsx from "clsx";
import Link from "next/link";
import { fmtCompact, fmtNum, hrefFor } from "@/lib/format";
import type { ThesisCell } from "@/lib/types";
import { DemoMark } from "../DemoMark";
import { TrendGlyph } from "../TrendGlyph";
import { WatchTopicButton } from "./WatchTopicButton";

export const GAP_MOMENTUM_MIN = 60;
export const GAP_ATTENTION_MAX = 40;

/** Cells with no linked entities, papers or roles at all. */
export function isQuiet(c: ThesisCell): boolean {
  return c.momentum === 0 && c.companies === 0 && c.founders === 0 && c.papers_30d === 0 && c.hiring_roles_30d === 0;
}

/** Attention is the mean percentile of linked companies — undefined without any. */
export function attentionMeasured(c: ThesisCell): boolean {
  return c.attention !== null && c.attention !== undefined;
}

const MOMENTUM_TITLE =
  "Momentum = 100 × (0.7 × mean technical-momentum + acceleration share of the top-5 linked companies + 0.3 × signal growth share, last 14d vs prior 14d)";
const ATTENTION_TITLE =
  "Attention = mean public-attention percentile of linked companies (HN, stars velocity, followers where measured)";

function Stat({ label, value, sub, title }: { label: string; value: number; sub?: string; title: string }) {
  return (
    <div className="min-w-0 px-2.5 py-2 first:pl-0" title={title}>
      <dt className="label truncate text-[9px] tracking-[0.04em] text-ink-3">{label}</dt>
      <dd className={clsx("tnum mt-0.5 font-mono text-[13px]", value === 0 ? "text-ink-3" : "text-ink")}>
        {fmtCompact(value)}
        {sub && <span className="ml-1 text-[10px] text-ink-3">{sub}</span>}
      </dd>
    </div>
  );
}

function Meter({ value, tone, label }: { value: number | null; tone: "ink" | "muted"; label: string }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="grid grid-cols-[62px_1fr] items-center gap-2">
      <span className="label text-[9.5px] text-ink-3">{label}</span>
      <div className="relative h-[6px]" aria-hidden>
        <div className="absolute inset-x-0 top-[2.5px] h-px bg-rule" />
        {value !== null && (
          <div
            className={clsx("absolute inset-y-0 left-0", tone === "ink" ? "bg-ink" : "hatch border-0 bg-panel")}
            style={{ width: `${pct}%` }}
          />
        )}
        {/* gap thresholds */}
        <div
          className="absolute -top-[2px] h-[10px] w-px bg-ink-3/60"
          style={{ left: `${label === "Momentum" ? GAP_MOMENTUM_MIN : GAP_ATTENTION_MAX}%` }}
        />
      </div>
    </div>
  );
}

export interface ThesisCellViewProps {
  cell: ThesisCell;
  watchItemId: string | null;
}

/** One subcategory of the thesis: momentum, attention, counts, top entities and a watch toggle. */
export function ThesisCellView({ cell: c, watchItemId }: ThesisCellViewProps) {
  const measured = attentionMeasured(c);
  const searchHref = `/search?q=${encodeURIComponent(c.label)}`;
  return (
    <article
      id={`cell-${c.key}`}
      data-card
      data-href={searchHref}
      tabIndex={0}
      aria-labelledby={`cell-${c.key}-label`}
      className={clsx(
        "relative flex min-w-0 scroll-mt-28 flex-col border-b border-r border-rule px-5 pb-4 pt-4 transition-colors hover:bg-panel/40",
        c.attention_gap && "bg-accent-wash/60",
      )}
    >
      {c.attention_gap && <span aria-hidden className="absolute inset-y-0 left-0 w-[2px] bg-accent" />}
      <header className="flex items-start justify-between gap-3">
        <h3 id={`cell-${c.key}-label`} className="min-w-0 font-serif text-[19px] leading-[1.15] tracking-[-0.005em] text-ink">
          <Link href={searchHref} tabIndex={-1} className="decoration-accent decoration-1 underline-offset-4 hover:underline">
            {c.label}
          </Link>
        </h3>
        <WatchTopicButton topicKey={c.key} itemId={watchItemId} className="mt-1 shrink-0" />
      </header>

      <div className="mt-3 flex items-end justify-between gap-4">
        <div className="flex items-baseline gap-2" title={MOMENTUM_TITLE}>
          <span
            className={clsx(
              "tnum font-serif text-[40px] leading-[0.9] tracking-[-0.02em]",
              c.attention_gap ? "text-accent" : c.momentum >= 50 ? "text-ink" : "text-ink-2",
            )}
          >
            {fmtNum(c.momentum, 0)}
          </span>
          <TrendGlyph trend={c.momentum_trend} accent={c.attention_gap} className="text-[14px]" />
          <span className="sr-only">momentum</span>
        </div>
        <div className="text-right" title={ATTENTION_TITLE}>
          <p className="label text-[9.5px] text-ink-3">Investor attention</p>
          {measured ? (
            <p className="tnum mt-0.5 font-mono text-[13px] text-ink">
              p{fmtNum(c.attention, 0)} <TrendGlyph trend={c.attention_trend} kind="attention" className="ml-0.5" />
            </p>
          ) : (
            <p className="mt-0.5 font-mono text-[11px] text-ink-3" title="No linked companies, so attention cannot be measured">
              not measured
            </p>
          )}
        </div>
      </div>

      <div className="mt-3 space-y-1.5">
        <Meter value={c.momentum} tone="ink" label="Momentum" />
        <Meter value={measured ? c.attention : null} tone="muted" label="Attention" />
      </div>

      <p
        className={clsx("mt-3 font-mono text-[10px] uppercase tracking-[0.08em]", c.attention_gap ? "text-accent" : "text-ink-3")}
        title={`Flagged when momentum ≥ ${GAP_MOMENTUM_MIN} and attention ≤ ${GAP_ATTENTION_MAX} with at least one linked company`}
      >
        {c.attention_gap
          ? "◆ Possible attention gap"
          : !measured
            ? c.momentum > 0
              ? "Momentum from paper & people signals only"
              : " "
            : c.momentum >= GAP_MOMENTUM_MIN
              ? "Attention already priced in"
              : " "}
      </p>

      <dl className="mt-2 grid grid-cols-4 divide-x divide-rule border-y border-rule">
        <Stat
          label="Emerging"
          value={c.emerging_companies}
          sub={c.companies ? `/${c.companies}` : undefined}
          title={`${c.emerging_companies} emerging of ${c.companies} linked companies (formation/pre-seed, stealth/project, or first seen ≤ 45 days)`}
        />
        <Stat label="Founders" value={c.founders} title="People tagged with this subcategory" />
        <Stat
          label="Papers 30d"
          value={c.papers_30d}
          title="Papers tagged with this subcategory, published in the last 30 days"
        />
        <Stat
          label="Roles 30d"
          value={c.hiring_roles_30d}
          title="Technical roles at linked companies first seen in the last 30 days"
        />
      </dl>

      <div className="mt-3 min-h-[20px]">
        {c.top_entities.length > 0 ? (
          <ul className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px] leading-snug">
            {c.top_entities.map((e) => (
              <li key={`${e.type}-${e.id ?? e.slug}`} className="inline-flex items-baseline gap-1">
                <Link
                  href={hrefFor(e.type, e.slug ?? e.id ?? e.name)}
                  className="link inline-block max-w-[24ch] truncate align-bottom text-ink"
                  title={e.name}
                >
                  {e.name}
                </Link>
                {e.score !== null && e.score !== undefined && (
                  <span className="tnum font-mono text-[10px] text-ink-3">{e.score}</span>
                )}
                {e.is_demo && <DemoMark />}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12px] text-ink-3">No scored entities yet.</p>
        )}
      </div>
    </article>
  );
}
