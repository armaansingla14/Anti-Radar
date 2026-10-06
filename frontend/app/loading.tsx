import { SectionLabel } from "@/components/SectionLabel";
import { Skeleton, SkeletonCard } from "@/components/Skeleton";

/** Root loading state: the editorial frame with quiet placeholders, no spinners. */
export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite" className="pb-24">
      <span className="sr-only">Loading the radar…</span>
      <p className="label mb-3 text-ink-3">Compiling</p>
      <Skeleton className="h-[52px] w-[min(520px,80%)]" />
      <Skeleton className="mt-5 h-5 w-[min(640px,90%)]" />
      <Skeleton className="mt-2 h-5 w-[min(420px,70%)]" />
      <Skeleton className="mt-5 h-3 w-[min(560px,85%)]" />
      <div className="mt-8 flex gap-5 border-b border-rule pb-3">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-3 w-16" />
        ))}
      </div>
      <div className="mt-10 grid gap-x-14 gap-y-12 xl:grid-cols-[minmax(0,1fr)_316px]">
        <div>
          <SectionLabel index={1}>Loading</SectionLabel>
          <div className="mt-8">
            <SkeletonCard />
            <SkeletonCard />
          </div>
        </div>
        <div className="space-y-4">
          <Skeleton className="h-3 w-40" />
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="grid grid-cols-[44px_1fr] gap-3 border-t border-rule pt-3">
              <Skeleton className="h-6 w-9" />
              <Skeleton lines={2} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
