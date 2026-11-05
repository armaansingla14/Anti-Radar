import clsx from "clsx";
import Link from "next/link";
import type { SinceSnapshot } from "@/lib/types";
import { sinceView } from "./logic";

/**
 * Score change since the previous verified snapshot, for an entity header: "+11 since Oct 20".
 * Only when the backend marks it comparable; otherwise a quiet "not compared" note with the reason.
 * Renders nothing on a single-snapshot database.
 */
export function SinceScore({ since, className }: { since: SinceSnapshot | null | undefined; className?: string }) {
  const v = sinceView(since);
  if (v.kind === "none") return null;
  if (v.kind === "not-compared") {
    return (
      <p className={clsx("text-right font-mono text-[10.5px] leading-snug text-ink-3", className)} data-since="not-compared">
        <span className="uppercase tracking-[0.06em]">{v.text}</span>
        <span className="block normal-case">{v.reason}</span>
      </p>
    );
  }
  return (
    <p className={clsx("flex items-baseline justify-end gap-2 font-mono text-[11.5px]", className)} data-since={v.tone}>
      <Link
        href="/changes#scores"
        title={v.title}
        className={clsx(
          "tnum whitespace-nowrap underline-offset-4 hover:underline",
          v.tone === "up" ? "text-accent" : v.tone === "down" ? "text-ink" : "text-ink-2",
        )}
      >
        <span aria-hidden className="mr-1">{v.tone === "up" ? "↑" : v.tone === "down" ? "↓" : "→"}</span>
        {v.text}
      </Link>
    </p>
  );
}
