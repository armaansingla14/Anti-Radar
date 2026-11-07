import type { Metadata } from "next";
import { PortfolioTour } from "@/components/demo/PortfolioTour";

export const metadata: Metadata = {
  title: "Portfolio demo",
  description: "A guided tour of Anti Radar's Portfolio Pulse over real public data.",
};

/**
 * /demo/portfolio — presenter mode for Portfolio Pulse: what changed after we cared, one company, one material
 * event, its competitive map, the diligence handoff, the brief and the agent's audit trail. ← → move, H hides the bar.
 */
export default function PortfolioDemoPage() {
  return <PortfolioTour />;
}
