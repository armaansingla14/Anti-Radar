"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { FeedbackMenu } from "@/components/FeedbackMenu";
import { useCommand } from "@/components/KeyboardProvider";
import { useToast } from "@/components/Toast";
import { WatchButton } from "@/components/WatchButton";
import { postFeedback, postMemo } from "@/lib/api";
import type { FeedbackLabel } from "@/lib/types";
import { MeetingBriefButton } from "@/components/meet/MeetingBriefButton";
import { InvestigationPanel } from "./InvestigationPanel";

export interface EntityActionsProps {
  entityType: "company" | "person";
  entityId: string;
  entityName: string;
  watched: boolean;
  /** Open the investigation panel immediately (?investigate=1). */
  autoInvestigate?: boolean;
  /** False when the backend reports no LLM provider — outputs are templates. */
  llmEnabled?: boolean | null;
  /** People only: latest cached meeting brief (opened, never regenerated on load). */
  meetingBriefId?: string | null;
  /** Session is read-only — write actions point to /unlock. */
  readOnly?: boolean;
}

const act =
  "inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-ink disabled:cursor-wait disabled:opacity-60";
const primary =
  "inline-flex items-center gap-1.5 border px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] transition-colors disabled:cursor-wait disabled:opacity-60";

function setInvestigateParam(on: boolean) {
  const url = new URL(window.location.href);
  if (on) url.searchParams.set("investigate", "1");
  else url.searchParams.delete("investigate");
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
}

/**
 * Entity action bar: Watch, Investigate (inline analyst panel),
 * Generate Anti Memo, feedback and dismiss. Registers palette commands.
 */
export function EntityActions({ entityType, entityId, entityName, watched, autoInvestigate, llmEnabled, meetingBriefId = null, readOnly = false }: EntityActionsProps) {
  const router = useRouter();
  const toast = useToast();
  const [investigating, setInvestigating] = useState(!!autoInvestigate);
  const [runId, setRunId] = useState(0);
  const [memoBusy, setMemoBusy] = useState(false);
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const [dismissBusy, setDismissBusy] = useState(false);
  const listPath = entityType === "company" ? "/companies" : "/founders";
  const templateOnly = llmEnabled === false;

  const investigate = useCallback(() => {
    if (investigating) {
      document.getElementById("investigation")?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    setInvestigating(true);
    setRunId((n) => n + 1);
    setInvestigateParam(true);
  }, [investigating]);

  const closeInvestigation = useCallback(() => {
    setInvestigating(false);
    setInvestigateParam(false);
  }, []);

  const memo = useCallback(async () => {
    if (memoBusy) return;
    setMemoBusy(true);
    try {
      const r = await postMemo({ entity_type: entityType, entity_id: entityId });
      toast(`Anti Memo ready${r.generator === "template" ? " — template output" : ""}`);
      router.push(`/reports/${r.id}`);
    } catch {
      toast("Could not generate the memo", { tone: "error" });
      setMemoBusy(false);
    }
  }, [memoBusy, entityType, entityId, toast, router]);

  const feedback = async (label: FeedbackLabel) => {
    try {
      await postFeedback({ entity_type: entityType, entity_id: entityId, label });
      toast("Feedback recorded — it will adjust future rankings");
    } catch (err) {
      toast("Could not record feedback", { tone: "error" });
      throw err;
    }
  };

  const dismiss = async () => {
    setDismissBusy(true);
    try {
      await postFeedback({ entity_type: entityType, entity_id: entityId, label: "dismiss" });
      toast(`${entityName} dismissed — hidden from radar feeds`);
      router.push(listPath);
    } catch {
      toast("Could not dismiss", { tone: "error" });
      setDismissBusy(false);
      setConfirmDismiss(false);
    }
  };

  useCommand({ id: `entity-investigate-${entityId}`, label: `Investigate ${entityName}`, hint: "I", keywords: "analyst brief", run: investigate });
  useCommand({ id: `entity-memo-${entityId}`, label: `Generate Anti Memo — ${entityName}`, keywords: "memo report", run: () => void memo() });

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-y border-ink py-3">
        <WatchButton entityType={entityType} entityId={entityId} watched={watched} size="md" />
        <button
          type="button"
          data-action="investigate"
          onClick={investigate}
          className={clsx(primary, investigating ? "border-accent bg-accent text-paper hover:bg-accent-hover" : "border-ink text-ink hover:bg-ink hover:text-paper")}
          aria-expanded={investigating}
        >
          <span aria-hidden>{investigating ? "●" : "◆"}</span>
          {investigating ? "Investigating" : "Investigate"}
        </button>
        <button
          type="button"
          onClick={() => void memo()}
          disabled={memoBusy}
          className={clsx(primary, "border-ink text-ink hover:bg-ink hover:text-paper")}
          title={templateOnly ? "LLM disabled — the memo will be a deterministic template from database facts" : undefined}
        >
          <span aria-hidden>▤</span>
          {memoBusy ? "Drafting memo…" : "Generate Anti Memo"}
        </button>
        {entityType === "person" && <MeetingBriefButton personId={entityId} briefId={meetingBriefId} readOnly={readOnly} allowRegenerate />}
        <FeedbackMenu onSelect={feedback} />
        {confirmDismiss ? (
          <span className="inline-flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.08em]">
            <span className="text-ink-2">Hide from all feeds?</span>
            <button type="button" onClick={() => void dismiss()} disabled={dismissBusy} className="text-accent hover:underline">
              {dismissBusy ? "Dismissing…" : "Confirm"}
            </button>
            <button type="button" onClick={() => setConfirmDismiss(false)} className="text-ink-2 hover:text-ink">
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setConfirmDismiss(true)} className={act}>
            Dismiss
          </button>
        )}
        {templateOnly && (
          <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3" title="No LLM provider is configured on this API instance">
            Live LLM off — cached or template output
          </span>
        )}
      </div>

      {investigating && (
        <InvestigationPanel
          key={runId}
          entityType={entityType}
          entityId={entityId}
          entityName={entityName}
          onClose={closeInvestigation}
        />
      )}
    </>
  );
}
