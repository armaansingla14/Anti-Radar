import clsx from "clsx";
import Link from "next/link";
import { fmtSigned } from "@/lib/format";
import type { ThesisShift } from "@/lib/types";
import { themeLabel } from "./prose";

/** ↑↑ strong heating, ↑ heating, ↓ cooling. */
function arrow(s: ThesisShift): string {
  if (s.direction === "cooling") return "↓";
  return s.momentum >= 20 ? "↑↑" : "↑";
}

export interface ThesisShiftListProps {
  shifts: ThesisShift[];
  className?: string;
}

/** Thesis areas heating or cooling this week, with the computed reason. */
export function ThesisShiftList({ shifts, className }: ThesisShiftListProps) {
  return (
    <ul className={clsx("divide-y divide-rule", className)}>
      {shifts.map((s) => {
        const heating = s.direction === "heating";
        return (
          <li key={s.topic_key} className="grid grid-cols-[30px_1fr_auto] gap-x-2 py-3 first:pt-0">
            <span
              aria-label={heating ? "Heating" : "Cooling"}
              className={clsx("pt-[2px] font-mono text-[13px] leading-none", heating ? "text-accent" : "text-ink-3")}
            >
              {arrow(s)}
            </span>
            <div className="min-w-0">
              <p className="text-[13.5px] font-medium leading-snug text-ink">{s.label}</p>
              <p className="mt-0.5 text-[12px] leading-snug text-ink-2">{s.detail}</p>
              <Link href={`/?theme=${encodeURIComponent(s.theme)}`} className="label mt-1 inline-block text-ink-3 hover:text-accent">
                {themeLabel(s.theme)} →
              </Link>
            </div>
            <span
              className={clsx("tnum font-mono text-[11px]", heating ? "text-ink" : "text-ink-3")}
              title="Momentum change (thesis-map units)"
            >
              {fmtSigned(s.momentum, 1)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
