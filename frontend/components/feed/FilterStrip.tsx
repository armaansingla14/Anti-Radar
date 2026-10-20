import clsx from "clsx";
import Link from "next/link";
import type { HomeWindow } from "@/lib/types";
import { WINDOW_TABS, windowHref } from "./homeLogic";

export interface FilterStripProps {
  window: HomeWindow | null;
  /** Right-aligned note (e.g. "J / K to move"). */
  right?: React.ReactNode;
}

const base = "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150 whitespace-nowrap";
const on = "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]";
const off = "text-ink-2 hover:text-ink";

/** ALL · NEW TODAY · NEW THIS WEEK (UNKNOWN YESTERDAY). Plain links, so they work without JS. */
export function FilterStrip({ window, right }: FilterStripProps) {
  return (
    <nav aria-label="Home filters" className="flex flex-wrap items-baseline gap-x-6 gap-y-3 border-b border-rule pb-3">
      {WINDOW_TABS.map((t) => {
        const active = t.key === window;
        return (
          <Link
            key={t.label}
            href={windowHref(t.key)}
            className={clsx(base, active ? on : off)}
            aria-current={active ? "page" : undefined}
            title={t.title}
          >
            {t.label}
            {t.key === "week" && <span className={clsx("ml-1.5 normal-case tracking-normal", active ? "text-ink-2" : "text-ink-3")}>(unknown yesterday)</span>}
          </Link>
        );
      })}
      {right && <span className="ml-auto font-mono text-[10.5px] text-ink-3">{right}</span>}
    </nav>
  );
}
