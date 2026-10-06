import clsx from "clsx";
import type { ReactNode } from "react";
import { Delta } from "@/components/Delta";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { Sparkline } from "@/components/Sparkline";
import { fmtDate } from "@/lib/format";
import type { AntiScore, ScorePoint } from "@/lib/types";
import { WhyButton } from "./WhyButton";

export interface EntityHeaderProps {
  eyebrow: ReactNode;
  name: string;
  lede?: string | null;
  /** Tags, stage, links… under the lede. */
  meta?: ReactNode;
  score: AntiScore | null;
  /** Fallback number when no breakdown exists (e.g. summary score). */
  scoreValue?: number | null;
  scoreLabel: "Anti Score" | "Founder Score";
  history: ScorePoint[];
  delta7d?: number | null;
  /** Fictional entity: evidence links never point at real hosts. */
  isDemo?: boolean;
}

/** Editorial entity header: big serif name on the left, the score column on the right. */
export function EntityHeader({ eyebrow, name, lede, meta, score, scoreValue, scoreLabel, history, delta7d, isDemo = false }: EntityHeaderProps) {
  const total = score?.total ?? scoreValue ?? null;
  const d7 = score?.delta_7d ?? delta7d ?? null;
  const d30 = score?.delta_30d ?? null;
  return (
    <header className="grid grid-cols-12 gap-x-10 gap-y-8 pb-7">
      <div className="col-span-12 min-w-0 lg:col-span-8">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">{eyebrow}</div>
        <h1 className="mt-4 font-serif text-[60px] leading-[0.98] tracking-[-0.02em] text-ink max-md:text-[44px]">{name}</h1>
        {lede && <p className="mt-4 max-w-[60ch] font-serif text-[21px] italic leading-snug text-ink-2">{lede}</p>}
        {meta && <div className="mt-6">{meta}</div>}
      </div>
      <aside
        className="col-span-12 flex min-w-0 flex-col gap-4 border-t border-ink pt-4 lg:col-span-4 lg:border-l lg:border-t-0 lg:border-rule lg:pl-8 lg:pt-1"
        aria-label={`${scoreLabel} ${total ?? "unscored"}`}
      >
        <div className="flex items-end justify-between gap-4">
          <ScoreNumeral value={total} size="xl" accent label={scoreLabel} />
          <div className="flex flex-col items-end gap-1.5 pb-2">
            {d30 !== null && <Delta value={d30} period="this month" size="md" />}
            <Delta value={d7} period="this week" size="sm" accentAt={Infinity} />
          </div>
        </div>
        {history.length > 1 && (
          <div>
            <Sparkline values={history.map((p) => p.score)} width={260} height={40} label={`${scoreLabel} since ${fmtDate(history[0].day, { year: false })}`} className="max-w-full" />
          </div>
        )}
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <WhyButton score={score} entityName={name} scoreLabel={scoreLabel} isDemo={isDemo} />
          {score && (
            <span className={clsx("tnum font-mono text-[10px] uppercase tracking-[0.06em]", score.low_coverage ? "text-accent" : "text-ink-3")}>
              {score.low_coverage ? "Low coverage · " : ""}v{score.version} · n={score.cohort_size}
            </span>
          )}
        </div>
      </aside>
    </header>
  );
}
