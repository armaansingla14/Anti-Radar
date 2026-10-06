import clsx from "clsx";
import type { ReactNode } from "react";

export interface StatTileProps {
  value: ReactNode;
  label: string;
  /** Small line beneath, e.g. a Delta or "since 05 OCT". */
  sub?: ReactNode;
  accent?: boolean;
  className?: string;
}

/** Big serif number with a mono caption — for count rows and KPIs. */
export function StatTile({ value, label, sub, accent, className }: StatTileProps) {
  return (
    <div className={clsx("flex min-w-0 flex-col gap-1.5 border-t border-ink pt-3", className)}>
      <span className="label">{label}</span>
      <span className={clsx("tnum font-serif text-[40px] leading-none tracking-[-0.02em]", accent ? "text-accent" : "text-ink")}>
        {value}
      </span>
      {sub && <span className="text-[12px] text-ink-2">{sub}</span>}
    </div>
  );
}
