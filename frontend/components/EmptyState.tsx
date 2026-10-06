import clsx from "clsx";
import type { ReactNode } from "react";

export interface EmptyStateProps {
  title: string;
  children?: ReactNode;
  /** Optional action row (links/buttons). */
  action?: ReactNode;
  className?: string;
}

/** Honest, typographic empty state — say what is missing and why. */
export function EmptyState({ title, children, action, className }: EmptyStateProps) {
  return (
    <div className={clsx("border-y border-rule py-10 text-center", className)} role="status">
      <p className="label mb-3">— Nothing to show —</p>
      <p className="font-serif text-[22px] leading-tight text-ink">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-[52ch] text-[13.5px] leading-relaxed text-ink-2">{children}</div>}
      {action && <div className="mt-5 flex justify-center gap-4">{action}</div>}
    </div>
  );
}
