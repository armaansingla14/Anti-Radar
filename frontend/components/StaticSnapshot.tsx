"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * True when this deployment serves the bundled verified snapshot (DATA_SOURCE=snapshot): there is no
 * backend to write to, so write controls (watch, feedback, dismiss, investigate, ingest) render nothing
 * or link to the stored report instead of failing.
 */
const StaticSnapshotContext = createContext(false);

export function StaticSnapshotProvider({ value, children }: { value: boolean; children: ReactNode }) {
  return <StaticSnapshotContext.Provider value={value}>{children}</StaticSnapshotContext.Provider>;
}

export function useStaticSnapshot(): boolean {
  return useContext(StaticSnapshotContext);
}
