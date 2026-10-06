import { SectionLabel } from "@/components/SectionLabel";
import { Skeleton } from "@/components/Skeleton";

/** Route-level loading placeholder with the proportions of an insight page. */
export function PageSkeleton({ label, sections = 2 }: { label: string; sections?: number }) {
  return (
    <div aria-busy="true" aria-label={`Loading ${label}`}>
      <p className="label mb-3">{label} · loading</p>
      <Skeleton className="mb-4 h-11 w-2/3 max-w-[620px]" />
      <Skeleton lines={2} className="mb-12 max-w-[560px]" />
      <div className="mb-14 grid grid-cols-2 gap-6 md:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-2 border-t border-ink pt-3">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-9 w-16" />
          </div>
        ))}
      </div>
      {Array.from({ length: sections }, (_, i) => (
        <div key={i} className="mb-14">
          <SectionLabel index={i + 1}>
            <span className="text-ink-3">Loading</span>
          </SectionLabel>
          <Skeleton className="mt-6 h-48 w-full" />
        </div>
      ))}
    </div>
  );
}
