"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/Toast";
import { ApiError, postMeetingBrief } from "@/lib/api";
import { briefAction, briefErrorText } from "./logic";

const base =
  "inline-flex items-center gap-1.5 border px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] transition-colors disabled:cursor-wait disabled:opacity-60";

export interface MeetingBriefButtonProps {
  personId: string;
  /** Latest existing meeting brief, if any (cached — opening it costs nothing). */
  briefId: string | null;
  readOnly: boolean;
  /** Offer "Regenerate" next to an existing brief (writes allowed only). */
  allowRegenerate?: boolean;
  /** "solid" = the primary action of a surface. */
  tone?: "solid" | "outline";
  className?: string;
}

/**
 * GENERATE MEETING BRIEF → POST /api/meeting-brief then open /reports/<id>.
 * Shows OPEN MEETING BRIEF when one is cached, and "Unlock to generate" when
 * this session is read-only. Never generates on page load.
 */
export function MeetingBriefButton({ personId, briefId, readOnly, allowRegenerate, tone = "outline", className }: MeetingBriefButtonProps) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState<"generate" | "regenerate" | null>(null);
  const action = briefAction({ brief_report_id: briefId }, readOnly);
  const cls = clsx(
    base,
    tone === "solid" ? "border-ink bg-ink text-paper hover:border-accent hover:bg-accent" : "border-ink text-ink hover:bg-ink hover:text-paper",
  );

  const run = async (force: boolean) => {
    if (busy) return;
    setBusy(force ? "regenerate" : "generate");
    try {
      const r = await postMeetingBrief(personId, force);
      toast(`Meeting brief ready${r.generator === "template" ? " — template output" : ""}`);
      router.push(`/reports/${encodeURIComponent(r.id)}`);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : null;
      toast(briefErrorText(status, err instanceof ApiError ? err.detail : null), { tone: "error" });
      setBusy(null);
    }
  };

  if (action.kind === "open") {
    return (
      <span className={clsx("inline-flex flex-wrap items-center gap-x-4 gap-y-2", className)}>
        <Link href={action.href} data-action="brief" className={cls}>
          <span aria-hidden>▤</span> Open meeting brief
        </Link>
        {allowRegenerate && !readOnly && (
          <button
            type="button"
            onClick={() => void run(true)}
            disabled={!!busy}
            className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-3 transition-colors hover:text-ink disabled:cursor-wait"
            title="Draft a fresh brief from the latest evidence (the previous one is kept)"
          >
            {busy === "regenerate" ? "Regenerating…" : "Regenerate"}
          </button>
        )}
      </span>
    );
  }
  if (action.kind === "unlock") {
    return (
      <Link href={action.href} className={clsx(base, "border-rule-strong text-ink-2 hover:border-ink hover:text-ink", className)} title="This session is read-only">
        <span aria-hidden>⚿</span> Unlock to generate
      </Link>
    );
  }
  return (
    <button type="button" data-action="brief" onClick={() => void run(false)} disabled={!!busy} aria-busy={!!busy} className={clsx(cls, className)}>
      <span aria-hidden>▤</span>
      {busy ? "Drafting brief…" : "Generate meeting brief"}
    </button>
  );
}
