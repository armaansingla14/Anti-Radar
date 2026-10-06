"use client";

import clsx from "clsx";
import { useEffect, useRef } from "react";
import { domainOf, isSafeUrl } from "@/lib/format";
import type { InvestigateEvent } from "@/lib/types";

export interface StreamLogProps {
  events: InvestigateEvent[];
  running: boolean;
  title?: string;
  error?: string | null;
  /** Max height before the log scrolls. */
  maxHeight?: number;
  className?: string;
}

/** Glyph for an agent event, from its status (or type). */
export function eventGlyph(ev: InvestigateEvent): string {
  const s = (ev.status ?? ev.type ?? "").toLowerCase();
  if (s === "error" || s === "failed") return "×";
  if (s === "skipped") return "○";
  if (s === "done" || s === "complete" || s === "report" || s === "final") return "◆";
  if (s === "ok" || s === "success" || s === "result") return "●";
  return "→";
}

function eventText(ev: InvestigateEvent): string {
  if (ev.message) return ev.message;
  if (typeof ev.title === "string") return ev.title;
  if (typeof ev.detail === "string") return ev.detail;
  return ev.type;
}

/** Live agent trace: mono lines with step glyphs, auto-scrolling to the latest. */
export function StreamLog({ events, running, title = "Agent trace", error, maxHeight = 320, className }: StreamLogProps) {
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length, running]);

  return (
    <section className={clsx("border border-ink", className)} aria-label={title}>
      <header className="flex items-center justify-between border-b border-rule bg-panel px-3 py-1.5">
        <span className="label text-ink">{title}</span>
        <span className="label tnum">
          {running ? <span className="text-accent">● Live</span> : error ? <span className="text-accent">× Stopped</span> : events.length ? "◆ Complete" : "Idle"}
          <span className="ml-3 text-ink-3">{events.length} steps</span>
        </span>
      </header>
      <ol ref={listRef} className="overflow-y-auto px-3 py-2 font-mono text-[11.5px] leading-[1.75]" style={{ maxHeight }} aria-live="polite">
        {events.length === 0 && !running && <li className="text-ink-3">No steps yet.</li>}
        {events.map((ev, i) => {
          const glyph = eventGlyph(ev);
          return (
            <li key={i} className="grid animate-fade-in grid-cols-[16px_112px_1fr] gap-2">
              <span aria-hidden className={glyph === "×" ? "text-accent" : glyph === "◆" ? "text-ink" : "text-ink-2"}>
                {glyph}
              </span>
              <span className="truncate uppercase tracking-[0.04em] text-ink-3">{ev.step ?? ev.type}</span>
              <span className="min-w-0 break-words text-ink">
                {eventText(ev)}
                {isSafeUrl(ev.url) && (
                  <a href={ev.url} target="_blank" rel="noopener noreferrer nofollow" className="link ml-2 text-ink-2">
                    {domainOf(ev.url)} ↗
                  </a>
                )}
              </span>
            </li>
          );
        })}
        {running && (
          <li className="grid grid-cols-[16px_112px_1fr] gap-2 text-ink-3">
            <span aria-hidden>·</span>
            <span />
            <span className="caret">working</span>
          </li>
        )}
        {error && (
          <li className="grid grid-cols-[16px_112px_1fr] gap-2 text-accent" role="alert">
            <span aria-hidden>×</span>
            <span className="uppercase">error</span>
            <span>{error}</span>
          </li>
        )}
      </ol>
    </section>
  );
}
