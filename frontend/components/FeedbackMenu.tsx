"use client";

import clsx from "clsx";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { FeedbackLabel } from "@/lib/types";

export const FEEDBACK_OPTIONS: { label: FeedbackLabel; text: string; hint: string }[] = [
  { label: "useful", text: "Useful", hint: "More like this" },
  { label: "not_interesting", text: "Not interesting", hint: "Less like this" },
  { label: "too_obvious", text: "Too obvious", hint: "Already consensus" },
  { label: "too_early", text: "Too early", hint: "Revisit later" },
  { label: "wrong_thesis", text: "Wrong thesis", hint: "Outside Anti Fund focus" },
  { label: "bad_data", text: "Bad data", hint: "Something here is wrong" },
  { label: "already_known", text: "Already known", hint: "In our pipeline" },
];

export interface FeedbackMenuProps {
  onSelect: (label: FeedbackLabel) => void | Promise<void>;
  /** Previously recorded label, if any. */
  selected?: FeedbackLabel | null;
  align?: "left" | "right";
  className?: string;
}

/** "Feedback" disclosure menu with the seven analyst labels. */
export function FeedbackMenu({ onSelect, selected = null, align = "left", className }: FeedbackMenuProps) {
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
    const idx = Math.max(0, FEEDBACK_OPTIONS.findIndex((o) => o.label === current));
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

  const currentText = FEEDBACK_OPTIONS.find((o) => o.label === current)?.text;

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
            "absolute z-40 mt-2 w-56 animate-rise border border-ink bg-paper py-1 rule-b",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {FEEDBACK_OPTIONS.map((o, i) => (
            <button
              key={o.label}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              type="button"
              role="menuitemradio"
              aria-checked={current === o.label}
              onClick={() => choose(o.label)}
              className="flex w-full items-baseline justify-between gap-3 px-3 py-1.5 text-left outline-none hover:bg-panel focus-visible:bg-panel"
            >
              <span className={clsx("text-[13px]", current === o.label ? "text-accent" : "text-ink")}>
                {current === o.label && "✓ "}
                {o.text}
              </span>
              <span className="font-mono text-[9.5px] uppercase tracking-[0.04em] text-ink-3">{o.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
