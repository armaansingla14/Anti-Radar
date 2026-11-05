"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useStaticSnapshot } from "@/components/StaticSnapshot";
import { useToast } from "@/components/Toast";
import { ApiError, postFailureReview } from "@/lib/api";
import type { FailureStatus } from "@/lib/types-agents";

const STATUSES: FailureStatus[] = ["OPEN", "CONFIRMED", "FIXED", "DISMISSED"];

/** Reviewer action on a failure (POST /api/proxy/agents/failures/{n}/review). Writable live backend only. */
export function FailureReviewActions({ number, current, writable }: { number: number; current: string; writable: boolean }) {
  const staticSnapshot = useStaticSnapshot();
  const router = useRouter();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<FailureStatus>((STATUSES as string[]).includes(current) ? (current as FailureStatus) : "CONFIRMED");
  const [busy, setBusy] = useState(false);
  if (!writable || staticSnapshot) return null;

  const submit = async () => {
    if (busy || !note.trim()) return;
    setBusy(true);
    try {
      await postFailureReview(number, { status, note: note.trim() });
      toast(`Recorded: ${status}`);
      setNote("");
      router.refresh();
    } catch (err) {
      toast(err instanceof ApiError && err.status === 403 ? "Read-only — unlock to review failures" : "Could not record the review", { tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="mt-4 space-y-2 border-t border-rule pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="label text-ink">Record review</p>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        required
        maxLength={4000}
        rows={3}
        placeholder="What you checked and why (required)"
        className="w-full resize-y border border-rule bg-paper px-2 py-1.5 text-[12.5px] text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none"
      />
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as FailureStatus)}
          aria-label="Status"
          className="border border-rule bg-paper px-2 py-1 font-mono text-[11px] uppercase text-ink focus:border-ink focus:outline-none"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={busy || !note.trim()}
          className="border border-ink px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save review"}
        </button>
      </div>
    </form>
  );
}
