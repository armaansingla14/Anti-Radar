import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Demo video",
  description: "Anti Radar on video: the Portfolio Pulse walkthrough, the technical diligence tour and the radar walkthrough.",
};

const RELEASE = "https://github.com/armaansingla14/Anti-Radar/releases/tag/v1.0.0";
const RELEASE_MP4 = "https://github.com/armaansingla14/Anti-Radar/releases/download/v1.0.0/anti-radar-demo.mp4";

const LINKS = "mt-6 flex flex-wrap gap-x-8 gap-y-3 font-mono text-[11px] uppercase tracking-[0.08em]";
const DEK = "mt-3 max-w-[70ch] text-[15px] leading-relaxed text-ink-2";
const H2 = "font-serif text-[32px] leading-[1.1] tracking-[-0.01em] md:text-[40px]";

/** The demo videos, newest first. Served from this deployment (the radar video also from the GitHub release). */
export default function VideoPage() {
  return (
    <div className="pb-20">
      <section id="portfolio">
        <p className="label mb-3">Portfolio Pulse · 2.5 min</p>
        <h1 className="font-serif text-[40px] leading-[1.05] tracking-[-0.01em] md:text-[52px]">What changed after we cared.</h1>
        <p className={DEK}>
          A captioned, silent walkthrough recorded on the verified snapshot of Nov 6, 2026. It covers selected publicly
          disclosed Anti Fund portfolio companies and their competitors. Material changes are separated from noise by
          documented rules, and each one shows why it matters and which questions to review. The walkthrough also shows a
          portfolio event handed to technical diligence, the morning brief, and how the portfolio agent is evaluated.
        </p>
        <div className="mt-8 border border-ink bg-ink">
          <video controls playsInline preload="metadata" className="block aspect-video w-full" data-demo="portfolio-video">
            <source src="/demo/anti-radar-portfolio-demo.mp4" type="video/mp4" />
            Your browser cannot play this video. <a href="/demo/anti-radar-portfolio-demo.mp4">Download the MP4</a>.
          </video>
        </div>
        <div className={LINKS}>
          <Link href="/portfolio" className="link text-ink">
            Open Portfolio Pulse →
          </Link>
          <Link href="/demo/portfolio" className="link text-ink-2">
            Take the portfolio tour →
          </Link>
        </div>
      </section>

      <section className="mt-16 border-t border-rule pt-10" id="diligence">
        <p className="label mb-3">Technical diligence · 2 min</p>
        <h2 className={H2}>Radar finds it. Diligence tests it.</h2>
        <p className={DEK}>
          A captioned, silent recording of the diligence tour on the snapshot of Nov 5, 2026. It shows a company that weakened
          on the radar, the material claims it makes, the public evidence for and against each one, and a generated (never
          executed) test plan. It also covers technical underwriting, questions for the founders, and the evaluation of the
          agents that did the work.
        </p>
        <div className="mt-8 border border-ink bg-ink">
          <video controls playsInline preload="metadata" className="block aspect-video w-full" data-demo="diligence-video">
            <source src="/demo/anti-radar-diligence-demo.mp4" type="video/mp4" />
            Your browser cannot play this video. <a href="/demo/anti-radar-diligence-demo.mp4">Download the MP4</a>.
          </video>
        </div>
        <div className={LINKS}>
          <Link href="/diligence" className="link text-ink">
            Open diligence →
          </Link>
          <Link href="/demo/diligence" className="link text-ink-2">
            Take the diligence tour →
          </Link>
        </div>
      </section>

      <section className="mt-16 border-t border-rule pt-10" id="radar">
        <p className="label mb-3">The radar · 3 min · narrated</p>
        <h2 className={H2}>Anti Radar in three minutes.</h2>
        <p className={DEK}>
          A narrated walkthrough of the radar tour, recorded on the first verified snapshot (Oct 20, 2026), so its numbers
          predate the later snapshots. It covers the morning view, Formation Radar, why each candidate surfaced, who to meet
          this week, the attention gap and a source-cited Anti Brief.
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
        <div className={LINKS}>
          <Link href="/" className="link text-ink">
            Open the radar →
          </Link>
          <Link href="/demo" className="link text-ink-2">
            Take the radar tour →
          </Link>
          <a href={RELEASE} className="link text-ink-2">
            Download from GitHub ↗
          </a>
        </div>
      </section>
    </div>
  );
}
