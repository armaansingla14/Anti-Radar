import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { EpistemicBadge } from "@/components/Badges";
import { SectionLabel } from "@/components/SectionLabel";
import { StatTile } from "@/components/StatTile";
import { PageHeader } from "@/components/insight/PageHeader";
import { TrendGlyph } from "@/components/insight/TrendGlyph";
import {
  GAP_ATTENTION_MAX,
  GAP_MOMENTUM_MIN,
  ThesisCellView,
  attentionMeasured,
  isQuiet,
} from "@/components/insight/thesis/ThesisCellView";
import { ThesisScatter, type ScatterPoint } from "@/components/insight/thesis/ThesisScatter";
import { getThesis, getWatchlist } from "@/lib/api";
import { fmtCompact, fmtDateTime, fmtNum } from "@/lib/format";
import type { ThesisCell, ThesisTheme, WatchlistResponse } from "@/lib/types";

export const metadata: Metadata = { title: "Thesis map" };

function themeStats(t: ThesisTheme) {
  const active = t.cells.filter((c) => !isQuiet(c));
  return {
    active: active.length,
    gaps: t.cells.filter((c) => c.attention_gap).length,
    companies: t.cells.reduce((s, c) => s + c.emerging_companies, 0),
    papers: t.cells.reduce((s, c) => s + c.papers_30d, 0),
    roles: t.cells.reduce((s, c) => s + c.hiring_roles_30d, 0),
    heating: t.cells.filter((c) => c.momentum_trend === "up2").length,
  };
}

function ThemeIndex({ themes }: { themes: ThesisTheme[] }) {
  const ranked = [...themes].sort((a, b) => b.momentum - a.momentum);
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="label text-ink">Themes by momentum</h3>
        <span className="label text-ink-3">Gaps</span>
      </div>
      <ol className="border-t border-ink">
        {ranked.map((t) => {
          const s = themeStats(t);
          return (
            <li key={t.key} className="border-b border-rule">
              <a href={`#theme-${t.key}`} className="group grid grid-cols-[1fr_44px_28px] items-center gap-3 py-2">
                <span className="min-w-0">
                  <span className="block truncate text-[13.5px] text-ink group-hover:text-accent">{t.label}</span>
                  <span className="mt-1 block h-[3px] bg-rule" aria-hidden>
                    <span className="block h-full bg-ink" style={{ width: `${Math.min(100, t.momentum)}%` }} />
                  </span>
                </span>
                <span className="tnum text-right font-serif text-[22px] leading-none text-ink">{fmtNum(t.momentum, 0)}</span>
                <span className={clsx("tnum text-right font-mono text-[11px]", s.gaps ? "text-accent" : "text-ink-3")}>
                  {s.gaps ? `◆${s.gaps}` : "—"}
                </span>
              </a>
            </li>
          );
        })}
      </ol>
      <p className="mt-2 text-[11.5px] leading-snug text-ink-3">
        Theme momentum is the mean of its subcategories weighted by (1 + linked companies), so empty cells count little.
      </p>
    </div>
  );
}

function QuietCells({ cells }: { cells: ThesisCell[] }) {
  if (!cells.length) return null;
  return (
    <p className="mt-3 text-[12.5px] leading-relaxed text-ink-3">
      <span className="label mr-2 text-ink-3">No activity observed</span>
      {cells.map((c, i) => (
        <span key={c.key}>
          {i > 0 && <span aria-hidden> · </span>}
          <Link href={`/search?q=${encodeURIComponent(c.label)}`} className="hover:text-ink">
            {c.label}
          </Link>
        </span>
      ))}
      <span className="ml-1">— no linked companies, people, papers or roles yet.</span>
    </p>
  );
}

function ThemeSection({ theme, index, watched }: { theme: ThesisTheme; index: number; watched: Map<string, string> }) {
  const s = themeStats(theme);
  const active = theme.cells.filter((c) => !isQuiet(c)).sort((a, b) => b.momentum - a.momentum);
  const quiet = theme.cells.filter(isQuiet);
  const themeTrend = active.length && s.heating / active.length >= 0.5 ? "up2" : s.heating > 0 ? "up" : "flat";
  return (
    <section id={`theme-${theme.key}`} className="mb-16 scroll-mt-6" aria-labelledby={`theme-${theme.key}-h`}>
      <SectionLabel index={index} right={`${s.active} of ${theme.cells.length} subcategories active`}>
        Theme
      </SectionLabel>
      <div className="mt-5 mb-6 grid grid-cols-12 items-end gap-x-8 gap-y-4">
        <div className="col-span-12 flex items-end gap-5 md:col-span-5">
          <h2 id={`theme-${theme.key}-h`} className="font-serif text-[34px] leading-none tracking-[-0.01em] text-ink">
            {theme.label}
          </h2>
          {theme.weight < 1 && (
            <span className="label mb-1 text-ink-3" title="Thesis weight — how strongly this theme counts toward thesis fit">
              Weight {fmtNum(theme.weight, 2)}
            </span>
          )}
        </div>
        <dl className="col-span-12 grid grid-cols-5 gap-4 md:col-span-7">
          <div title="Theme momentum: weighted mean of subcategory momentum">
            <dt className="label text-[9.5px]">Momentum</dt>
            <dd className="tnum mt-1 flex items-baseline gap-1.5 font-serif text-[30px] leading-none text-ink">
              {fmtNum(theme.momentum, 0)}
              <TrendGlyph trend={themeTrend} className="text-[12px]" />
            </dd>
          </div>
          {(
            [
              ["Gaps", s.gaps, "Subcategories flagged as a possible attention gap"],
              ["Emerging cos.", s.companies, "Sum of emerging companies across subcategories (a company can appear in several)"],
              ["Papers 30d", s.papers, "Sum of papers across subcategories (a paper can appear in several)"],
              ["Roles 30d", s.roles, "Technical roles first seen in 30 days at linked companies"],
            ] as const
          ).map(([label, v, title]) => (
            <div key={label} title={title}>
              <dt className="label text-[9.5px]">{label}</dt>
              <dd
                className={clsx(
                  "tnum mt-1.5 font-mono text-[17px] leading-none",
                  label === "Gaps" && v > 0 ? "text-accent" : v === 0 ? "text-ink-3" : "text-ink",
                )}
              >
                {fmtCompact(v)}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {active.length > 0 ? (
        <div className="grid grid-cols-1 border-l border-t border-rule md:grid-cols-2 xl:grid-cols-3">
          {active.map((c) => (
            <ThesisCellView key={c.key} cell={c} watchItemId={watched.get(c.key) ?? null} />
          ))}
        </div>
      ) : (
        <p className="border-y border-rule py-6 text-center text-[13.5px] text-ink-2">
          No subcategory in {theme.label} has any observed activity yet.
        </p>
      )}
      <QuietCells cells={quiet} />
    </section>
  );
}

function Method() {
  const rows: [string, string][] = [
    [
      "Momentum",
      "100 × (0.7 × mean share of technical-momentum + acceleration score components among the top-5 linked companies + 0.3 × signal growth share). Growth share = signals in the last 14 days ÷ signals in the last 28 days; 50 means flat.",
    ],
    ["Momentum arrow", "↑↑ signals at least doubled vs the prior 14 days · ↑ up 25%+ · → flat · ↓ down 25%+."],
    [
      "Investor attention",
      "Mean public-attention percentile of linked companies’ latest score snapshots. Undefined when a subcategory has no linked companies — shown as “not measured”, never as zero.",
    ],
    [
      "Possible attention gap",
      `Momentum ≥ ${GAP_MOMENTUM_MIN} and attention ≤ ${GAP_ATTENTION_MAX} with at least one linked company. A prompt to look, not a conclusion.`,
    ],
    ["Emerging", "Linked companies at formation/pre-seed, stealth or project stage, or first seen within 45 days."],
    ["Counts", "Founders = people tagged with the subcategory; papers and technical roles are first-seen within 30 days."],
  ];
  return (
    <section className="mt-4" aria-labelledby="thesis-method">
      <SectionLabel id="thesis-method" right={<EpistemicBadge value="INFERENCE" />}>
        Method — how every number on this page is computed
      </SectionLabel>
      <dl className="mt-5 grid gap-x-10 gap-y-4 md:grid-cols-2">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[150px_1fr] gap-4 border-t border-rule pt-3">
            <dt className="label text-ink">{k}</dt>
            <dd className="text-[13px] leading-relaxed text-ink-2">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default async function ThesisPage() {
  const [map, watchlist] = await Promise.all([getThesis(), getWatchlist().catch((): WatchlistResponse | null => null)]);

  const watched = new Map<string, string>();
  for (const it of watchlist?.items ?? []) if (it.entity_type === "topic" && it.topic_key) watched.set(it.topic_key, it.id);

  const cells = map.themes.flatMap((t) => t.cells.map((c) => ({ ...c, themeLabel: t.label })));
  const gaps = cells.filter((c) => c.attention_gap);
  const activeCount = cells.filter((c) => !isQuiet(c)).length;
  const heating = cells.filter((c) => c.momentum_trend === "up2" && !isQuiet(c)).length;
  const papers = cells.reduce((s, c) => s + c.papers_30d, 0);

  const points: ScatterPoint[] = cells.filter(attentionMeasured).map((c) => ({
    key: c.key,
    label: c.label,
    theme: c.themeLabel,
    momentum: c.momentum,
    attention: c.attention,
    companies: c.companies,
    gap: c.attention_gap,
  }));
  const unplotted = cells.filter((c) => !attentionMeasured(c) && !isQuiet(c)).length;
  const topGap = [...gaps].sort((a, b) => b.momentum - b.attention - (a.momentum - a.attention))[0];

  return (
    <div>
      <PageHeader
        kicker={
          <>
            Thesis map · {map.themes.length} themes · {cells.length} subcategories · computed {fmtDateTime(map.generated_at)}
          </>
        }
        title={
          gaps.length > 0 ? (
            <>
              {gaps.length} {gaps.length === 1 ? "area shows" : "areas show"} momentum{" "}
              <em className="italic text-accent">ahead of attention</em>.
            </>
          ) : (
            <>Where technical momentum meets investor attention.</>
          )
        }
        dek={
          <>
            Every subcategory of the Anti Fund thesis, scored deterministically from what the radar has observed — linked
            companies’ score components, signal growth over 14 days, papers and technical hiring.
            {topGap && (
              <>
                {" "}
                The widest gap is{" "}
                <a href={`#cell-${topGap.key}`} className="link text-ink">
                  {topGap.label}
                </a>{" "}
                ({topGap.themeLabel}): momentum {fmtNum(topGap.momentum, 0)} against attention p{fmtNum(topGap.attention, 0)}.
              </>
            )}
          </>
        }
      />

      <div className="mb-14 grid grid-cols-2 gap-6 md:grid-cols-4">
        <StatTile
          value={gaps.length}
          label="Possible attention gaps"
          accent={gaps.length > 0}
          sub={`momentum ≥ ${GAP_MOMENTUM_MIN}, attention ≤ ${GAP_ATTENTION_MAX}`}
        />
        <StatTile value={heating} label="Subcategories doubling" sub="signals ≥ 2× prior 14 days" />
        <StatTile value={`${activeCount}/${cells.length}`} label="Subcategories active" sub="any linked entity, paper or role" />
        <StatTile value={fmtCompact(papers)} label="Tagged papers · 30d" sub="summed across subcategories" />
      </div>

      <section className="mb-16" aria-labelledby="thesis-overview">
        <SectionLabel index={1} id="thesis-overview" right={`${points.length} plotted`}>
          Momentum versus attention
        </SectionLabel>
        <div className="mt-6 grid grid-cols-12 gap-x-10 gap-y-8">
          <div className="col-span-12 lg:col-span-8">
            {points.length > 1 ? (
              <ThesisScatter points={points} gapMomentum={GAP_MOMENTUM_MIN} gapAttention={GAP_ATTENTION_MAX} />
            ) : (
              <p className="border-y border-rule py-10 text-center text-[13.5px] text-ink-2">
                Not enough subcategories with linked companies to plot momentum against attention yet.
              </p>
            )}
            <p className="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
              <span>
                <span className="text-accent">●</span> Possible attention gap
              </span>
              <span>○ Other subcategory</span>
              <span>Dot area ∝ linked companies</span>
              {unplotted > 0 && (
                <span>{unplotted} active subcategories not plotted — no linked companies, attention unmeasurable</span>
              )}
            </p>
          </div>
          <aside className="col-span-12 lg:col-span-4">
            <ThemeIndex themes={map.themes} />
          </aside>
        </div>
      </section>

      {map.themes.map((t, i) => (
        <ThemeSection key={t.key} theme={t} index={i + 2} watched={watched} />
      ))}

      <Method />
    </div>
  );
}
