import clsx from "clsx";
import type { ReactNode } from "react";

export interface PageHeaderProps {
  /** Mono eyebrow above the title. */
  kicker: ReactNode;
  title: ReactNode;
  /** Standfirst paragraph under the title. */
  dek?: ReactNode;
  /** Right-hand column (stats, legend, actions). */
  aside?: ReactNode;
  className?: string;
}

/** Editorial page opener: eyebrow, serif headline, standfirst and an optional right rail. */
export function PageHeader({ kicker, title, dek, aside, className }: PageHeaderProps) {
  return (
    <header className={clsx("mb-12 grid grid-cols-12 gap-x-10 gap-y-6", className)}>
      <div className={clsx("col-span-12 min-w-0", aside && "lg:col-span-8")}>
        <p className="label mb-3">{kicker}</p>
        <h1 className="max-w-[22ch] font-serif text-[46px] leading-[1.02] tracking-[-0.015em] text-ink">{title}</h1>
        {dek && <div className="mt-4 max-w-[64ch] text-[15.5px] leading-relaxed text-ink-2">{dek}</div>}
      </div>
      {aside && <div className="col-span-12 min-w-0 self-end lg:col-span-4">{aside}</div>}
    </header>
  );
}
