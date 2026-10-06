import clsx from "clsx";

/** Inline, compact DEMO marker for dense lists (DemoBadge is the full chip). */
export function DemoMark({ className }: { className?: string }) {
  return (
    <span
      className={clsx(
        "hatch inline-block px-[3px] align-[1px] font-mono text-[8.5px] uppercase leading-[12px] tracking-[0.08em] text-ink-2",
        className,
      )}
      title="Synthetic demo data — fictional entity"
    >
      Demo
    </span>
  );
}
