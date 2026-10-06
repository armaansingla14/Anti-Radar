import clsx from "clsx";
import Link from "next/link";
import type { TopicTag } from "@/lib/types";

export interface ThesisTagProps {
  tag: Pick<TopicTag, "label" | "theme"> & Partial<Pick<TopicTag, "key" | "score" | "method">>;
  /** Show the theme prefix ("ROBOTICS /"). */
  showTheme?: boolean;
  href?: string;
  className?: string;
}

/** Thesis taxonomy tag: quiet mono theme prefix + label. */
export function ThesisTag({ tag, showTheme = true, href, className }: ThesisTagProps) {
  const title = [tag.key, tag.method && `tagged by ${tag.method}`, tag.score !== undefined && `fit ${tag.score.toFixed(2)}`]
    .filter(Boolean)
    .join(" · ");
  const body = (
    <>
      {showTheme && <span className="text-ink-3">{tag.theme.replace(/_/g, " ")} /</span>}
      <span className="text-ink">{tag.label}</span>
    </>
  );
  const cls = clsx(
    "inline-flex items-baseline gap-1 border-b border-rule pb-[1px] font-mono text-[10.5px] uppercase tracking-[0.06em]",
    href && "transition-colors hover:border-accent",
    className,
  );
  return href ? (
    <Link href={href} className={cls} title={title}>
      {body}
    </Link>
  ) : (
    <span className={cls} title={title}>
      {body}
    </span>
  );
}
