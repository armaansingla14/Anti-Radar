import { Skeleton } from "@/components/Skeleton";

function RowSkeleton() {
  return (
    <div className="grid grid-cols-12 gap-x-8 gap-y-4 border-t border-rule pb-9 pt-5" aria-hidden>
      <div className="col-span-12 md:col-span-1">
        <Skeleton className="h-12 w-12" />
      </div>
      <div className="col-span-12 space-y-3 md:col-span-8">
        <Skeleton className="h-3 w-56" />
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-3 w-2/3" />
        <div className="grid gap-6 pt-3 lg:grid-cols-2">
          <Skeleton lines={3} />
          <Skeleton lines={3} />
        </div>
      </div>
      <div className="col-span-12 space-y-2 md:col-span-3 md:border-l md:border-rule md:pl-6">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-8 w-12" />
      </div>
    </div>
  );
}

export default function Loading() {
  return (
    <div aria-busy="true" aria-live="polite" className="pb-24">
      <span className="sr-only">Loading this week&apos;s meeting list…</span>
      <p className="label mb-3 text-accent">This week</p>
      <h1 className="max-w-[17ch] font-serif text-[46px] leading-[1.0] tracking-[-0.02em] text-ink md:text-[58px]">
        Who should we meet this week?
      </h1>
      <Skeleton className="mt-5 h-5 w-[min(560px,85%)]" />
      <Skeleton className="mt-4 h-3 w-[min(360px,60%)]" />
      <div className="mt-12">
        <RowSkeleton />
        <RowSkeleton />
        <RowSkeleton />
      </div>
    </div>
  );
}
