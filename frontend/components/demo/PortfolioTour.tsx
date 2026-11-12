"use client";

import { DemoPresenter, type TourConfig } from "./DemoPresenter";
import { PORTFOLIO_SCENE_COUNT, type PortfolioPlan } from "./portfolioPlan";
import { warmPortfolioPlan } from "./portfolioWarm";

const PORTFOLIO_TOUR: TourConfig = {
  sceneCount: PORTFOLIO_SCENE_COUNT,
  load: warmPortfolioPlan,
  stageTitle: "Anti Radar — Portfolio Pulse",
  title: {
    tagline: "Portfolio Pulse.",
    dek: "What changed after we cared — material events, competitor moves and open questions across selected publicly disclosed Anti Fund portfolio companies, each with its source, its rule and what to review.",
    readyLine: "Ready · nine scenes · about two and a half minutes",
  },
  final: (p) => {
    const plan = p as PortfolioPlan | null;
    const c = plan?.counts;
    return {
      heading: (
        <span className="block text-[0.74em] leading-[1.02]">
          DISCOVER. DILIGENCE.
          <br />
          MONITOR. <span className="text-accent">DECIDE.</span>
        </span>
      ),
      stats: [
        { n: c?.tracked ?? null, label: "portfolio companies and comps monitored from public sources" },
        { n: c?.raw_changes ?? null, label: `raw changes in 30 days, filtered by rule to ${c?.material ?? "—"} material events` },
        { n: c?.pinned ?? null, label: "real portfolio-agent failures, each pinned by a regression test" },
      ],
    };
  },
};

/** /demo/portfolio presenter (client: the tour config carries functions). */
export function PortfolioTour() {
  return <DemoPresenter tour={PORTFOLIO_TOUR} />;
}
