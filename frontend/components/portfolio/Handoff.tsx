import Link from "next/link";
import { RunDiligenceButton } from "@/components/diligence/RunButton";
import { diligenceHref } from "@/components/diligence/logic";

/**
 * The RUN DILIGENCE handoff into V3: "Open diligence →" when a workspace exists; on a writable live backend a run
 * button (POST /api/diligence/{slug}/run); otherwise a quiet note — never a dead button.
 */
export function DiligenceHandoff({ slug, hasWorkspace, writable }: { slug: string; hasWorkspace: boolean; writable: boolean }) {
  if (hasWorkspace)
    return (
      <Link href={diligenceHref(slug)} className="link font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink">
        Open diligence →
      </Link>
    );
  if (writable) return <RunDiligenceButton slug={slug} writable={writable} />;
  return <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">Diligence not run · runs on a writable live backend</span>;
}
