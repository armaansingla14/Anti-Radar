import { Suspense } from "react";
import { getStatus } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { providerHealthLine } from "@/lib/labels";
import type { SystemStatus } from "@/lib/types";

async function loadStatus(): Promise<SystemStatus | null> {
  try {
    return await Promise.race([
      getStatus(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), 2500)),
    ]);
  } catch {
    return null;
  }
}

async function Freshness() {
  const status = await loadStatus();
  if (!status) return <span>Data freshness unavailable — backend not reachable</span>;
  return (
    <span className="tnum">
      Scored {status.last_scored_at ? fmtDateTime(status.last_scored_at) : "— never"} · score {status.score_version} ·{" "}
      {providerHealthLine(status.providers)}
      {status.demo_mode && <span className="ml-2 text-ink-2">· demo mode</span>}
    </span>
  );
}

/** Footer with a live data-freshness line. */
export function Footer() {
  return (
    <footer className="mx-auto mt-24 w-full max-w-[1240px] px-6 pb-10 md:px-10">
      <div className="border-t border-ink pt-3" />
      <div className="flex flex-wrap items-baseline justify-between gap-3 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-3">
        <span>
          <span className="text-ink">Anti Radar</span> · Anti Fund internal · Signals are not facts until sourced
        </span>
        <Suspense fallback={<span>Checking data freshness…</span>}>
          <Freshness />
        </Suspense>
        <span>
          Press <kbd className="text-ink-2">?</kbd> for shortcuts
        </span>
      </div>
    </footer>
  );
}
