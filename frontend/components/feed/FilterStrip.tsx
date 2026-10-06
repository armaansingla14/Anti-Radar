import clsx from "clsx";
import Link from "next/link";
import { fmtCompact } from "@/lib/format";
import { THEMES } from "./prose";

export interface FilterStripProps {
  newOnly: boolean;
  theme: string | null;
  /** Entities first seen in 7 days; null when unknown (count is then omitted). */
  newThisWeek: number | null;
}

function href(newOnly: boolean, theme: string | null): string {
  const sp = new URLSearchParams();
  if (newOnly) sp.set("new", "1");
  if (theme) sp.set("theme", theme);
  const qs = sp.toString();
  return qs ? `/?${qs}` : "/";
}

const base =
  "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150 whitespace-nowrap";
const on =
  "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]";
const off = "text-ink-2 hover:text-ink";

/** Quiet text toggles: ALL · UNKNOWN YESTERDAY | themes. Plain links, so they work without JS. */
export function FilterStrip({ newOnly, theme, newThisWeek }: FilterStripProps) {
  const all = !newOnly && !theme;
  return (
    <nav
      aria-label="Radar filters"
      className="flex flex-wrap items-baseline gap-x-5 gap-y-3 border-b border-rule pb-3"
    >
      <Link
        href="/"
        className={clsx(base, all ? on : off)}
        aria-current={all ? "page" : undefined}
      >
        All
      </Link>
      <Link
        href={href(!newOnly, theme)}
        className={clsx(base, newOnly ? on : off)}
        aria-current={newOnly ? "true" : undefined}
        title="Entities first seen in the last 7 days"
      >
        Unknown yesterday{" "}
        {newThisWeek !== null && (
          <span
            className={clsx(
              "tnum normal-case tracking-normal",
              newOnly ? "text-accent" : "text-ink-3",
            )}
          >
            ({fmtCompact(newThisWeek)} new this week)
          </span>
        )}
      </Link>
      <span aria-hidden className="h-3 w-px self-center bg-rule-strong" />
      {THEMES.map((t) => {
        const active = theme === t.key;
        return (
          <Link
            key={t.key}
            href={href(newOnly, active ? null : t.key)}
            className={clsx(base, active ? on : off)}
            aria-current={active ? "true" : undefined}
            title={active ? `Clear ${t.label} filter` : `Show only ${t.label}`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
