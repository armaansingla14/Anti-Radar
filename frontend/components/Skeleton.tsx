import clsx from "clsx";

export interface SkeletonProps {
  className?: string;
  /** Render N stacked text lines instead of a single block. */
  lines?: number;
}

/** Quiet placeholder block; pulses gently. */
export function Skeleton({ className, lines }: SkeletonProps) {
  if (lines) {
    return (
      <div className={clsx("space-y-2", className)} aria-hidden>
        {Array.from({ length: lines }, (_, i) => (
          <div key={i} className="h-3 animate-pulse bg-panel" style={{ width: `${i === lines - 1 ? 62 : 100 - i * 6}%` }} />
        ))}
      </div>
    );
  }
  return <div aria-hidden className={clsx("animate-pulse bg-panel", className ?? "h-4 w-full")} />;
}

/** Placeholder with the proportions of an IntelCard. */
export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={clsx("grid grid-cols-12 gap-6 border-t border-rule py-7", className)} aria-hidden>
      <div className="col-span-12 space-y-4 md:col-span-8">
        <Skeleton className="h-3 w-48" />
        <Skeleton className="h-7 w-2/3" />
        <Skeleton lines={3} />
      </div>
      <div className="col-span-12 space-y-3 md:col-span-4">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-14 w-24" />
        <Skeleton className="h-6 w-full" />
      </div>
    </div>
  );
}
