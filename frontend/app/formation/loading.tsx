import { SectionLabel } from "@/components/SectionLabel";
import { Skeleton } from "@/components/Skeleton";

function CardSkeleton() {
  return (
    <div className="grid grid-cols-12 gap-x-8 gap-y-4 border-t border-rule pb-7 pt-4" aria-hidden>
      <div className="col-span-12 space-y-3 md:col-span-9">
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-7 w-1/2" />
        <Skeleton className="h-3 w-2/3" />
        <div className="space-y-2 pt-3">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="grid grid-cols-[52px_24px_1fr] gap-3">
              <Skeleton className="h-3 w-10" />
              <Skeleton className="h-3 w-5" />
              <Skeleton className="h-3" />
            </div>
          ))}
        </div>
      </div>
      <div className="col-span-12 space-y-2 md:col-span-3 md:border-l md:border-rule md:pl-6">
        <Skeleton className="ml-auto h-3 w-24" />
        <Skeleton className="ml-auto h-8 w-12" />
      </div>
    </div>
  );
}

export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite" className="pb-24">
      <span className="sr-only">Loading the formation radar…</span>
      <p className="label mb-3 text-ink">Formation Radar</p>
      <h1 className="max-w-[20ch] font-serif text-[46px] leading-[1.0] tracking-[-0.02em] text-ink md:text-[58px]">
        Technical teams before the company announcement.
      </h1>
      <Skeleton className="mt-5 h-5 w-[min(520px,85%)]" />
      <Skeleton className="mt-4 h-3 w-[min(360px,60%)]" />
      <div className="mt-8 flex gap-5 border-b border-rule pb-3">
        {Array.from({ length: 9 }, (_, i) => (
          <Skeleton key={i} className="h-3 w-16" />
        ))}
      </div>
      <div className="mt-12">
        <SectionLabel index={1}>
          <span className="text-ink-3">High confidence</span>
        </SectionLabel>
        <div className="mt-8">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      </div>
    </div>
  );
}
