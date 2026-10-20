import clsx from "clsx";
import { ScoreBar } from "@/components/ScoreBar";
import { fmtNum } from "@/lib/format";
import type { FormationConvergence, ScoreComponent } from "@/lib/types";
import { convergenceStrength, familyWords } from "./logic";

/** TEAM CONVERGENCE summary: strength word, 0..1 score, explanation, prior ties and families. */
export function ConvergenceSummary({ c }: { c: FormationConvergence }) {
  const strength = convergenceStrength(c);
  const fams = familyWords(c.families);
  return (
    <div>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3 border-t border-ink pt-3">
        <div>
          <p className="label">Strength</p>
          <p className={clsx("mt-1 font-serif text-[34px] leading-none tracking-[-0.01em]", strength === "HIGH" ? "text-accent" : "text-ink")}>
            {strength.charAt(0) + strength.slice(1).toLowerCase()}
          </p>
        </div>
        <div>
          <p className="label">Convergence</p>
          <p className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{fmtNum(c.score, 2)}</p>
        </div>
        <div>
          <p className="label">Prior ties</p>
          <p className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{c.prior_relationships}</p>
        </div>
        {fams.length > 0 && (
          <div className="pb-1">
            <p className="label">Families</p>
            <p className="mt-1.5 font-mono text-[11px] uppercase tracking-[0.06em] text-ink-2">{fams.join(" · ")}</p>
          </div>
        )}
      </div>
      <ScoreBar value={c.score} max={1} className="mt-3 max-w-[420px]" accent={strength === "HIGH"} />
      <p className="mt-4 max-w-[68ch] text-[14px] leading-relaxed text-ink">{c.explanation}</p>
    </div>
  );
}

/** Compact component bars for the report header (full detail lives in the Why drawer). */
export function ComponentBars({ components }: { components: ScoreComponent[] }) {
  if (components.length === 0) return null;
  return (
    <ul className="space-y-2.5">
      {components.map((c) => (
        <li key={c.key}>
          <ScoreBar value={c.value} max={c.max} label={c.label} showValue />
        </li>
      ))}
    </ul>
  );
}
