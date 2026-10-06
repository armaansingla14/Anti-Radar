import { Skeleton } from "@/components/Skeleton";

/** Skeleton with the proportions of an entity page. */
export function EntityLoading() {
  return (
    <div aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-12 gap-10 pb-7">
        <div className="col-span-12 space-y-4 lg:col-span-8">
          <Skeleton className="h-3 w-56" />
          <Skeleton className="h-14 w-3/4" />
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
        <div className="col-span-12 space-y-3 lg:col-span-4 lg:border-l lg:border-rule lg:pl-8">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-20 w-28" />
          <Skeleton className="h-8 w-full" />
        </div>
      </div>
      <div className="h-[46px] border-y border-ink" />
      <div className="mt-12 grid gap-12 lg:grid-cols-[7fr_5fr]">
        <Skeleton lines={5} />
        <Skeleton lines={7} />
      </div>
    </div>
  );
}

/** Skeleton for index tables. */
export function IndexLoading({ title }: { title: string }) {
  return (
    <div aria-busy="true" aria-label={`Loading ${title}`}>
      <p className="label mb-3">{title}</p>
      <Skeleton className="mb-8 h-12 w-72" />
      <Skeleton className="mb-10 h-9 w-full" />
      <div className="border-t border-ink">
        {Array.from({ length: 10 }, (_, i) => (
          <div key={i} className="flex items-center gap-6 border-b border-rule py-4">
            <Skeleton className="h-3 w-6" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="ml-auto h-5 w-10" />
          </div>
        ))}
      </div>
    </div>
  );
}
