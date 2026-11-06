"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useStaticSnapshot } from "@/components/StaticSnapshot";
import { useToast } from "@/components/Toast";
import { ApiError, postPortfolioQuestion, postPortfolioReview } from "@/lib/api";
import type { PortfolioDecision, QuestionStatus } from "@/lib/types-portfolio";
import { DECISIONS, decisionLabel, questionStatusLabel } from "./logic";

const btn =
  "border border-ink px-2 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-50";

function errorText(err: unknown, what: string): string {
  if (err instanceof ApiError && err.status === 403) return "Read-only — unlock to record reviews";
  if (err instanceof ApiError && err.status === 429) return "Rate limited — try again later";
  if (err instanceof ApiError) return `Could not record the ${what} (${err.detail})`;
  return `Could not record the ${what}`;
}

/**
 * MARK REVIEWED / NEEDS FOLLOW-UP / MONITOR / NO ACTION for a portfolio company (or one of its events), via
 * POST /api/proxy/portfolio/{company}/review. Rendered only on a writable live backend; nothing in the snapshot.
 */
export function PortfolioReviewActions({ slug, eventId, writable, label = "+ Record review" }: { slug: string; eventId?: string; writable: boolean; label?: string }) {
  const staticSnapshot = useStaticSnapshot();
  const router = useRouter();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState("analyst");
  const [busy, setBusy] = useState<PortfolioDecision | null>(null);
  if (!writable || staticSnapshot) return null;

  const submit = async (decision: PortfolioDecision) => {
    if (busy) return;
    setBusy(decision);
    try {
      await postPortfolioReview(slug, { decision, note: note.trim(), reviewer: reviewer.trim() || "analyst", event_id: eventId ?? null });
      toast(`Recorded: ${decisionLabel(decision)}`);
      setNote("");
      router.refresh();
    } catch (err) {
      toast(errorText(err, "review"), { tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <details className="mt-3 border-t border-rule pt-2 print:hidden">
      <summary className="label cursor-pointer list-none text-ink-2 hover:text-ink [&::-webkit-details-marker]:hidden">{label}</summary>
      <div className="mt-2 space-y-2">
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          rows={2}
          placeholder="Note (what you read, what is still open)"
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
            <button key={d} type="button" disabled={busy !== null} onClick={() => void submit(d)} className={btn}>
              {busy === d ? "…" : decisionLabel(d)}
            </button>
          ))}
        </div>
      </div>
    </details>
  );
}

const NEXT: Record<QuestionStatus, QuestionStatus[]> = {
  OPEN: ["ANSWERED", "DISMISSED"],
  ANSWERED: ["OPEN"],
  DISMISSED: ["OPEN"],
};

/** Mark a portfolio question answered / dismissed (or reopen it). Writable live backend only. */
export function QuestionActions({ id, status, writable }: { id: string; status: string; writable: boolean }) {
  const staticSnapshot = useStaticSnapshot();
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState<QuestionStatus | null>(null);
  if (!writable || staticSnapshot) return null;
  const current = questionStatusLabel(status) as QuestionStatus;

  const submit = async (next: QuestionStatus) => {
    if (busy) return;
    setBusy(next);
    try {
      await postPortfolioQuestion(id, { status: next, reviewer: "analyst" });
      toast(`Question ${next.toLowerCase()}`);
      router.refresh();
    } catch (err) {
      toast(errorText(err, "question status"), { tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <span className="inline-flex flex-wrap gap-1.5 print:hidden">
      {NEXT[current].map((s) => (
        <button key={s} type="button" disabled={busy !== null} onClick={() => void submit(s)} className={btn}>
          {busy === s ? "…" : s === "OPEN" ? "Reopen" : `Mark ${s.toLowerCase()}`}
        </button>
      ))}
    </span>
  );
}
