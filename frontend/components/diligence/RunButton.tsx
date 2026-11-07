"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useStaticSnapshot } from "@/components/StaticSnapshot";
import { useToast } from "@/components/Toast";
import { ApiError, postDiligenceRun } from "@/lib/api";
import { stateLabel } from "./logic";

/**
 * Start a live diligence run (POST /api/proxy/diligence/{slug}/run; synchronous, up to 3 minutes). Rendered
 * only on a writable live backend; nothing in the static snapshot.
 */
export function RunDiligenceButton({
  slug,
  writable,
  label = "Run diligence",
  eventId,
}: {
  slug: string;
  writable: boolean;
  label?: string;
  /** Portfolio handoff: the material event that prompted the run (its sources are collected too). */
  eventId?: string | null;
}) {
  const staticSnapshot = useStaticSnapshot();
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!writable || staticSnapshot) return null;

  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const s = await postDiligenceRun(slug, eventId);
      toast(`Diligence ${stateLabel(s.status).toLowerCase()}`);
      router.refresh();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 403
          ? "Read-only — unlock to run diligence"
          : err instanceof ApiError && err.status === 409
            ? "A run is already in progress"
            : err instanceof ApiError && err.status === 429
              ? "Rate limited — try again later"
              : "Diligence run failed";
      toast(msg, { tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void run()}
      disabled={busy}
      className="border border-ink px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60"
      title="Collect public artifacts and analyze them (up to 3 minutes). Untrusted code is never executed."
    >
      {busy ? "Running… up to 3 min" : label}
    </button>
  );
}
