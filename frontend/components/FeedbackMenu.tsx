"use client";

import clsx from "clsx";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { FEEDBACK_GROUPS, FEEDBACK_ITEMS, feedbackText } from "@/lib/labels";
import type { FeedbackLabel } from "@/lib/types";
import { useStaticSnapshot } from "@/components/StaticSnapshot";

/** Menu order: INTERESTING (+ reasons), NOT INTERESTING (+ reasons). */
export const FEEDBACK_OPTIONS = FEEDBACK_ITEMS;

export interface FeedbackMenuProps {
  onSelect: (label: FeedbackLabel) => void | Promise<void>;
  /** Previously recorded label, if any. */
  selected?: FeedbackLabel | null;
  align?: "left" | "right";
  className?: string;
}

/** "Feedback" disclosure menu: INTERESTING / NOT INTERESTING, each with optional reasons. */
export function FeedbackMenu({ onSelect, selected = null, align = "left", className }: FeedbackMenuProps) {
  const staticSnapshot = useStaticSnapshot();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<FeedbackLabel | null>(selected);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    const idx = Math.max(0, FEEDBACK_ITEMS.findIndex((o) => o.label === current));
    itemRefs.current[idx]?.focus();
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, current]);

  const choose = async (label: FeedbackLabel) => {
    setOpen(false);
    buttonRef.current?.focus();
    const previous = current;
    setCurrent(label);
    setBusy(true);
    try {
      await onSelect(label);
    } catch {
      setCurrent(previous);
    } finally {
      setBusy(false);
    }
  };

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter(Boolean) as HTMLButtonElement[];
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[next]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  const currentText = feedbackText(current);

  if (staticSnapshot) return null; // nothing to write to in the verified snapshot
  return (
    <div ref={rootRef} className={clsx("relative inline-block", className)}>
      <button
        ref={buttonRef}
        type="button"
        data-action="feedback"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        disabled={busy}
        onClick={() => setOpen((o) => !o)}
        className={clsx(
          "font-mono text-[10.5px] uppercase tracking-[0.08em] transition-colors hover:text-ink disabled:opacity-60",
          current ? "text-ink" : "text-ink-2",
        )}
      >
        {currentText ? `✓ ${currentText}` : "Feedback"} <span aria-hidden className="text-ink-3">▾</span>
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Rate this signal"
          onKeyDown={onMenuKey}
          className={clsx(
            "absolute z-40 mt-2 w-64 animate-rise border border-ink bg-paper py-1 rule-b",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {FEEDBACK_GROUPS.map((g, gi) => (
            <div key={g.key} role="group" aria-label={g.head.text} className={clsx(gi > 0 && "mt-1 border-t border-rule pt-1")}>
              {[g.head, ...g.reasons].map((o, j) => {
                const i = FEEDBACK_ITEMS.indexOf(o);
                const head = j === 0;
                return (
                  <button
                    key={o.label}
                    ref={(el) => {
                      itemRefs.current[i] = el;
                    }}
                    type="button"
                    role="menuitemradio"
                    aria-checked={current === o.label}
                    onClick={() => choose(o.label)}
                    className={clsx(
                      "flex w-full items-baseline gap-2 py-1.5 pr-3 text-left outline-none hover:bg-panel focus-visible:bg-panel",
                      head ? "pl-3" : "pl-7",
                    )}
                  >
                    <span
                      className={clsx(
                        head ? "font-mono text-[10.5px] uppercase tracking-[0.08em]" : "text-[13px]",
                        current === o.label ? "text-accent" : head ? "text-ink" : "text-ink-2",
                      )}
                    >
                      {current === o.label && "✓ "}
                      {o.text}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
