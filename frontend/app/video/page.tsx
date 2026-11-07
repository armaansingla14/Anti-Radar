import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Demo video",
  description: "Anti Radar on video: the radar walkthrough, the Portfolio Pulse tour and the technical diligence tour.",
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

      <section className="mt-16 border-t border-rule pt-10" id="portfolio">
        <p className="label mb-3">Portfolio Pulse · 2.5 min</p>
        <h2 className="font-serif text-[32px] leading-[1.1] tracking-[-0.01em] md:text-[40px]">What changed after we cared.</h2>
        <p className="mt-3 max-w-[70ch] text-[15px] leading-relaxed text-ink-2">
          A captioned, silent recording of the portfolio tour on the snapshot of Nov 6, 2026. It covers publicly disclosed
          Anti Fund portfolio companies and their competitors, separates material changes from noise, and shows why each change
          matters and which questions to review. It also shows a handoff from a portfolio event into technical diligence, the
          morning brief, and how the portfolio agent is evaluated.
        </p>
        <div className="mt-8 border border-ink bg-ink">
          <video controls playsInline preload="metadata" className="block aspect-video w-full" data-demo="portfolio-video">
            <source src="/demo/anti-radar-portfolio-demo.mp4" type="video/mp4" />
            Your browser cannot play this video. <a href="/demo/anti-radar-portfolio-demo.mp4">Download the MP4</a>.
          </video>
        </div>
        <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 font-mono text-[11px] uppercase tracking-[0.08em]">
          <Link href="/demo/portfolio" className="link text-ink">
            Take the portfolio tour →
          </Link>
          <Link href="/portfolio" className="link text-ink-2">
            Open Portfolio Pulse →
          </Link>
        </div>
      </section>

      <section className="mt-16 border-t border-rule pt-10" id="diligence">
        <p className="label mb-3">Technical diligence · 2 min</p>
        <h2 className="font-serif text-[32px] leading-[1.1] tracking-[-0.01em] md:text-[40px]">Radar finds it. Diligence tests it.</h2>
        <p className="mt-3 max-w-[70ch] text-[15px] leading-relaxed text-ink-2">
          A captioned, silent recording of the diligence tour on the snapshot of Nov 5, 2026: a company that weakened on the
          radar, the material claims it makes, the public evidence for and against each one, a generated (never executed)
          test plan, technical underwriting, questions for the founders, and the evaluation of the agents that did the work.
        </p>
        <div className="mt-8 border border-ink bg-ink">
          <video controls playsInline preload="metadata" className="block aspect-video w-full" data-demo="diligence-video">
            <source src="/demo/anti-radar-diligence-demo.mp4" type="video/mp4" />
            Your browser cannot play this video. <a href="/demo/anti-radar-diligence-demo.mp4">Download the MP4</a>.
          </video>
        </div>
        <div className="mt-6 flex flex-wrap gap-x-8 gap-y-3 font-mono text-[11px] uppercase tracking-[0.08em]">
          <Link href="/demo/diligence" className="link text-ink">
            Take the diligence tour →
          </Link>
          <Link href="/diligence" className="link text-ink-2">
            Open diligence →
          </Link>
        </div>
      </section>
    </div>
  );
}
