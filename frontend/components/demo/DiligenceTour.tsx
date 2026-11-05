"use client";

import { DemoPresenter, type TourConfig } from "./DemoPresenter";
import { DILIGENCE_SCENE_COUNT, type DiligencePlan } from "./diligencePlan";
import { warmDiligencePlan } from "./diligenceWarm";

const DILIGENCE_TOUR: TourConfig = {
  sceneCount: DILIGENCE_SCENE_COUNT,
  load: warmDiligencePlan,
  stageTitle: "Anti Radar — technical diligence",
  title: {
    tagline: "Technical Diligence.",
    dek: "What a company claims about its technology, the public evidence for and against it, and what remains unknown — and how far the agents doing the work can be trusted.",
    readyLine: "Ready · nine scenes · about two minutes",
  },
  final: (p) => {
    const plan = p as DiligencePlan | null;
    return {
      // Three lines at ~3/4 of the card's headline size, so "DILIGENCE TESTS IT." holds on one line.
      heading: (
        <span className="block text-[0.74em] leading-[1.02]">
          RADAR FINDS IT.
          <br />
          DILIGENCE TESTS IT.
          <br />
          <span className="text-accent">PEOPLE DECIDE.</span>
        </span>
      ),
      stats: [
        { n: plan?.counts.claims ?? null, label: "material claims, each with its evidence and status rule" },
        { n: plan?.counts.questions ?? null, label: "questions for the founders, each with why it is asked" },
        { n: plan?.counts.pinned ?? null, label: "documented agent failures pinned by a regression test" },
      ],
    };
  },
};

/** /demo/diligence presenter (client: the tour config carries functions). */
export function DiligenceTour() {
  return <DemoPresenter tour={DILIGENCE_TOUR} />;
}
