import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Demo video",
  description: "A three-minute narrated walkthrough of Anti Radar on the verified snapshot collected Oct 20, 2026.",
};

const RELEASE = "https://github.com/armaansingla14/Anti-Radar/releases/tag/v1.0.0";
const RELEASE_MP4 = "https://github.com/armaansingla14/Anti-Radar/releases/download/v1.0.0/anti-radar-demo.mp4";

/** The narrated demo (served from this deployment; the GitHub release copy is the fallback source). */
export default function VideoPage() {
  return (
    <div className="pb-20">
      <p className="label mb-3">Demo · 3 min</p>
      <h1 className="font-serif text-[40px] leading-[1.05] tracking-[-0.01em] md:text-[52px]">Anti Radar in three minutes.</h1>
      <p className="mt-3 max-w-[70ch] text-[15px] leading-relaxed text-ink-2">
        A narrated walkthrough of the guided tour, recorded on the verified snapshot collected Oct 20, 2026: the morning view,
        Formation Radar, why each candidate surfaced, who to meet this week, the attention gap and a source-cited Anti Brief.
      </p>
      <div className="mt-8 border border-ink bg-ink">
        <video
          controls
          playsInline
          preload="metadata"
          poster="/demo/poster.jpg"
          className="block aspect-video w-full"
          data-demo="video"
        >
          <source src="/demo/anti-radar-demo.mp4" type="video/mp4" />
          <source src={RELEASE_MP4} type="video/mp4" />
          Your browser cannot play this video. <a href={RELEASE_MP4}>Download the MP4</a>.
        </video>
      </div>
      <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 font-mono text-[11px] uppercase tracking-[0.08em]">
        <Link href="/" className="link text-ink">
          Open the radar →
        </Link>
        <Link href="/demo" className="link text-ink-2">
          Take the guided tour →
        </Link>
        <a href={RELEASE} className="link text-ink-2">
          Download from GitHub ↗
        </a>
      </div>
    </div>
  );
}
