import clsx from "clsx";
import { ageShort, dataAgeParts } from "@/lib/labels";
import type { DataAge as DataAgeData } from "@/lib/types";

export interface DataAgeProps {
  age: DataAgeData | null | undefined;
  /** Reference instant (ISO or Date). Pass the server's clock to keep SSR and hydration in agreement. */
  now: string | Date;
  className?: string;
}

/** "GitHub 8 min ago · Research 1 d ago · Hiring 19 h ago · X unavailable". */
export function DataAge({ age, now, className }: DataAgeProps) {
  const ref = typeof now === "string" ? new Date(now) : now;
  const parts = dataAgeParts(age, ref);
  const base = "font-mono text-[10px] uppercase leading-relaxed tracking-[0.06em] text-ink-3";
  if (parts.length === 0) {
    const updated = ageShort(age?.updated_at ?? null, ref);
    return (
      <p className={clsx(base, className)} suppressHydrationWarning>
        {updated ? `Record updated ${updated} · per-source age unavailable` : "Data age unavailable"}
      </p>
    );
  }
  return (
    <p className={clsx(base, className)} aria-label="Data age by source" suppressHydrationWarning>
      {parts.map((p, i) => (
        <span key={p.label} className="whitespace-nowrap">
          {i > 0 && <span aria-hidden> · </span>}
          <span className={p.available ? "text-ink-2" : undefined}>{p.label}</span> {p.value}
        </span>
      ))}
    </p>
  );
}
