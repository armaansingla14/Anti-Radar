"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useStaticSnapshot } from "@/components/StaticSnapshot";
import { useToast } from "@/components/Toast";
import { ApiError, postReview } from "@/lib/api";
import type { ReviewDecision, ReviewTargetType } from "@/lib/types-diligence";
import { DECISIONS, decisionLabel } from "./logic";

/**
 * Record a human review decision (POST /api/proxy/reviews). Rendered only on a writable live backend: the
 * page passes `writable` from the session; the static snapshot renders nothing.
 */
export function ReviewActions({ targetType, targetId, writable }: { targetType: ReviewTargetType; targetId: string; writable: boolean }) {
  const staticSnapshot = useStaticSnapshot();
  const router = useRouter();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState("analyst");
  const [busy, setBusy] = useState<ReviewDecision | null>(null);
  if (!writable || staticSnapshot) return null;

  const submit = async (decision: ReviewDecision) => {
    if (busy) return;
    setBusy(decision);
    try {
      await postReview({ target_type: targetType, target_id: targetId, decision, note: note.trim(), reviewer: reviewer.trim() || "analyst" });
      toast(`Recorded: ${decisionLabel(decision)}`);
      setNote("");
      router.refresh();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 403
          ? "Read-only — unlock to record reviews"
          : err instanceof ApiError
            ? `Could not record the review (${err.detail})`
            : "Could not record the review";
      toast(msg, { tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <details className="mt-3 border-t border-rule pt-2">
      <summary className="label cursor-pointer list-none text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">+ Record review</summary>
      <div className="mt-2 space-y-2">
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          rows={2}
          placeholder="Note (what you checked, what is still open)"
          className="w-full resize-y border border-rule bg-paper px-2 py-1.5 text-[12.5px] text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            maxLength={120}
            aria-label="Reviewer"
            className="w-28 border border-rule bg-paper px-2 py-1 font-mono text-[11px] text-ink focus:border-ink focus:outline-none"
          />
          {DECISIONS.map((d) => (
            <button
              key={d}
              type="button"
              disabled={busy !== null}
              onClick={() => void submit(d)}
              className="border border-ink px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-50"
            >
              {busy === d ? "…" : decisionLabel(d)}
            </button>
          ))}
        </div>
      </div>
    </details>
  );
}
