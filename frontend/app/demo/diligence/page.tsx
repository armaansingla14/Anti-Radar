import type { Metadata } from "next";
import { DiligenceTour } from "@/components/demo/DiligenceTour";

export const metadata: Metadata = {
  title: "Diligence demo",
  description: "A guided tour of Anti Radar's technical diligence and agent evaluation over real data.",
};

/**
 * /demo/diligence — presenter mode for technical diligence: one company from the radar into its diligence
 * workspace (claims, evidence, test plan, underwriting, founder questions), then the agents' own audit trail.
 * ← → move, H hides the presenter bar.
 */
export default function DiligenceDemoPage() {
  return <DiligenceTour />;
}
