import type { Metadata } from "next";
import Link from "next/link";
import { BriefActions } from "@/components/feed/BriefActions";
import { getBrief } from "@/lib/api";
import { loadBriefArchive } from "@/lib/api-pages";
import { BriefArchive } from "./BriefArchive";
import { BriefView } from "./BriefView";
import "./print.css";

export const metadata: Metadata = { title: "Morning brief" };

/** The brief composed live from current data, with the archive of stored editions. */
export default async function BriefPage() {
  const [brief, archive] = await Promise.all([getBrief(), loadBriefArchive()]);
  return (
    <div className="grid gap-x-14 gap-y-12 pb-24 xl:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0">
        <BriefView
          brief={brief}
          mode="live"
          isToday
          actions={
            <>
              <span className="flex items-center gap-x-5">
                <Link href="/" className="label text-ink-2 hover:text-ink">
                  ← Radar
                </Link>
                <Link href="/brief/today" className="label text-ink hover:text-accent">
                  Today&apos;s stored edition →
                </Link>
              </span>
              <BriefActions date={brief.date} />
            </>
          }
        />
      </div>
      <BriefArchive entries={archive} className="xl:pt-[52px]" />
    </div>
  );
}
