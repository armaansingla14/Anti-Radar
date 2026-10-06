"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { investigateStream } from "./api";
import type { InvestigateEvent, InvestigateRequest } from "./types";

export interface InvestigationState {
  events: InvestigateEvent[];
  running: boolean;
  error: string | null;
  /** report_id from the final event, when the backend provides one. */
  reportId: string | null;
  start: (req: InvestigateRequest) => Promise<void>;
  cancel: () => void;
}

/** Drive POST /api/investigate and collect its SSE events for <StreamLog>. */
export function useInvestigation(): InvestigationState {
  const [events, setEvents] = useState<InvestigateEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(async (req: InvestigateRequest) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setEvents([]);
    setError(null);
    setReportId(null);
    setRunning(true);
    try {
      await investigateStream(
        req,
        (ev) => {
          setEvents((xs) => [...xs, ev]);
          if (typeof ev.report_id === "string") setReportId(ev.report_id);
          if (ev.type === "error") setError(ev.message ?? "Investigation failed");
        },
        ctrl.signal,
      );
    } catch (err) {
      if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : "Investigation failed");
    } finally {
      if (abortRef.current === ctrl) setRunning(false);
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
  }, []);

  return { events, running, error, reportId, start, cancel };
}
