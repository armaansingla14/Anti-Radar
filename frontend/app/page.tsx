import { SectionLabel } from "@/components/SectionLabel";
import { Skeleton, SkeletonCard } from "@/components/Skeleton";

/** Placeholder until the Radar feed page lands. */
export default function Home() {
  return (
    <div aria-busy="true">
      <p className="label mb-3">Radar loading</p>
      <Skeleton className="mb-10 h-9 w-2/3 max-w-[560px]" />
      <div className="mb-12 grid grid-cols-2 gap-6 md:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="space-y-2 border-t border-ink pt-3">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-9 w-16" />
          </div>
        ))}
      </div>
      <SectionLabel index={1}>Today&apos;s radar</SectionLabel>
      <SkeletonCard />
      <SkeletonCard />
      <SkeletonCard />
    </div>
  );
}
