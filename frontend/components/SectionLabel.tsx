import clsx from "clsx";
import type { ReactNode } from "react";

export interface SectionLabelProps {
  children: ReactNode;
  /** Optional editorial index, rendered as "01". */
  index?: number;
  /** Right-aligned slot after the rule (counts, links). */
  right?: ReactNode;
  as?: "h2" | "h3" | "div";
  id?: string;
  className?: string;
}

/** Mono caps section label followed by a hairline rule. */
export function SectionLabel({ children, index, right, as: Tag = "h2", id, className }: SectionLabelProps) {
  return (
    <div className={clsx("flex items-center gap-3", className)}>
      <Tag id={id} className="label shrink-0 text-ink">
        {index !== undefined && <span className="tnum mr-2 text-ink-3">{String(index).padStart(2, "0")}</span>}
        {children}
      </Tag>
      <span aria-hidden className="h-px flex-1 bg-rule" />
      {right && <div className="label shrink-0">{right}</div>}
    </div>
  );
}
