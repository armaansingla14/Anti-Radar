import type { Metadata } from "next";
import { DemoPresenter } from "@/components/demo/DemoPresenter";

export const metadata: Metadata = {
  title: "Demo",
  description: "A guided tour of Anti Radar over real data — Conviction Before Consensus.",
};

/**
 * /demo — presenter mode. Selects the demo dataset (the verified snapshot in production), wakes the
 * backend, then walks ten scenes through the real app. ← → move, H hides the presenter bar.
 */
export default function DemoPage() {
  return <DemoPresenter />;
}
