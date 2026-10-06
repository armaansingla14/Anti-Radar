"use client";

import clsx from "clsx";
import { Fragment, useState } from "react";
import { fmtCompact, fmtDateTime, fmtNum } from "@/lib/format";
import type { ProviderState, ProviderStatus } from "@/lib/types";

export interface ProviderRow extends ProviderStatus {
  /** Pre-computed on the server ("4m ago") so client and server agree. */
  last_success_rel: string | null;
  last_run_rel: string | null;
}

const STATE: Record<ProviderState, { label: string; cls: string; glyph: string }> = {
  healthy: { label: "Healthy", cls: "text-ink", glyph: "●" },
  degraded: { label: "Degraded", cls: "text-ink", glyph: "◐" },
  error: { label: "Error", cls: "text-accent", glyph: "×" },
  disabled: { label: "Disabled", cls: "text-ink-3", glyph: "○" },
  never_run: { label: "Never run", cls: "text-ink-3", glyph: "·" },
};

const th = "label whitespace-nowrap px-3 py-2 text-left font-normal text-ink-3";
const td = "px-3 py-3 align-top";

/** Terminal-style provider table with expandable error logs. */
export function ProviderTable({ providers }: { providers: ProviderRow[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse border-y border-ink font-mono text-[12px]">
        <caption className="sr-only">Data providers</caption>
        <thead>
          <tr className="border-b border-ink">
            <th className={th} scope="col">
              Provider
            </th>
            <th className={th} scope="col">
              State
            </th>
            <th className={th} scope="col">
              Last success
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              Collected
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              Rejected
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              Signals
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              p50
            </th>
            <th className={clsx(th, "text-right")} scope="col" title="Remaining API quota reported by the provider">
              Rate left
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              Runs 24h
            </th>
            <th className={clsx(th, "text-right")} scope="col">
              Errors
            </th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => {
            const st = STATE[p.state] ?? STATE.never_run;
            const muted = p.state === "disabled" || p.state === "never_run";
            const isOpen = open.has(p.provider);
            return (
              <Fragment key={p.provider}>
                <tr className={clsx("border-b border-rule", muted && "text-ink-3")}>
                  <td className={clsx(td, "min-w-[220px] font-sans")}>
                    <div className={clsx("text-[14px]", muted ? "text-ink-2" : "text-ink")}>{p.label}</div>
                    <div className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{p.provider}</div>
                    {p.reason && (
                      <div
                        className={clsx(
                          "mt-1 max-w-[36ch] text-[11.5px] leading-snug",
                          p.state === "error" ? "text-accent" : "text-ink-2",
                        )}
                      >
                        {p.reason}
                      </div>
                    )}
                  </td>
                  <td className={clsx(td, "whitespace-nowrap uppercase tracking-[0.06em]", st.cls)}>
                    <span aria-hidden className="mr-1.5">
                      {st.glyph}
                    </span>
                    {st.label}
                  </td>
                  <td className={clsx(td, "whitespace-nowrap")}>
                    {p.last_success_at ? (
                      <span title={fmtDateTime(p.last_success_at)} className={muted ? undefined : "text-ink"}>
                        {p.last_success_rel}
                      </span>
                    ) : (
                      <span className="text-ink-3">never</span>
                    )}
                    {p.last_run_at && p.last_run_at !== p.last_success_at && (
                      <div className="text-[10.5px] text-ink-3" title={fmtDateTime(p.last_run_at)}>
                        last run {p.last_run_rel}
                      </div>
                    )}
                  </td>
                  <td className={clsx(td, "tnum text-right", !muted && "text-ink")}>{fmtCompact(p.records_collected)}</td>
                  <td className={clsx(td, "tnum text-right", p.records_rejected > 0 ? "text-ink-2" : "text-ink-3")}>
                    {fmtCompact(p.records_rejected)}
                  </td>
                  <td className={clsx(td, "tnum text-right", !muted && "text-ink")}>{fmtCompact(p.signals_created)}</td>
                  <td className={clsx(td, "tnum whitespace-nowrap text-right")}>
                    {p.latency_ms_p50 === null ? (
                      <span className="text-ink-3">—</span>
                    ) : p.latency_ms_p50 >= 1000 ? (
                      `${fmtNum(p.latency_ms_p50 / 1000, 1)}s`
                    ) : (
                      `${fmtNum(p.latency_ms_p50, 0)}ms`
                    )}
                  </td>
                  <td className={clsx(td, "tnum text-right")}>
                    {p.rate_limit_remaining === null ? (
                      <span className="text-ink-3" title="Provider does not report a quota">
                        —
                      </span>
                    ) : (
                      fmtCompact(p.rate_limit_remaining)
                    )}
                  </td>
                  <td className={clsx(td, "tnum text-right")}>{p.runs_24h}</td>
                  <td className={clsx(td, "text-right")}>
                    {p.errors.length ? (
                      <button
                        type="button"
                        onClick={() => toggle(p.provider)}
                        aria-expanded={isOpen}
                        aria-controls={`errors-${p.provider}`}
                        className="tnum whitespace-nowrap text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
                      >
                        {p.errors.length} {isOpen ? "▴" : "▾"}
                      </button>
                    ) : (
                      <span className="text-ink-3">0</span>
                    )}
                  </td>
                </tr>
                {isOpen && p.errors.length > 0 && (
                  <tr id={`errors-${p.provider}`} className="border-b border-rule bg-panel/50">
                    <td colSpan={10} className="px-3 py-3">
                      <ol className="animate-fade-in space-y-1.5 text-[11px] leading-relaxed text-ink-2">
                        {p.errors.map((e, i) => (
                          <li key={i} className="grid grid-cols-[28px_1fr] [overflow-wrap:anywhere]">
                            <span className="tnum text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                            <span>{e}</span>
                          </li>
                        ))}
                      </ol>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
