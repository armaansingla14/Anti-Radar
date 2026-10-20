"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/Toast";
import { ApiError, refreshMeet } from "@/lib/api";

/** Admin: recompute this week's recommendations (POST /api/meet/refresh). */
export function RefreshButton() {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const feed = await refreshMeet();
      toast(`Recomputed — ${feed?.people?.length ?? 0} recommendations this week`);
      router.refresh();
    } catch (err) {
      toast(err instanceof ApiError && err.status === 403 ? "Read-only — unlock to recompute" : "Could not recompute this week", { tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      onClick={() => void run()}
      disabled={busy}
      className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-3 transition-colors hover:text-ink disabled:cursor-wait"
      title="Re-rank this week from the latest scores and feedback"
    >
      {busy ? "Recomputing…" : "↻ Recompute this week"}
    </button>
  );
}
