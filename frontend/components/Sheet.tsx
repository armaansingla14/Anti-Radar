"use client";

import clsx from "clsx";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMounted } from "@/lib/useMounted";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** Accessible title; also rendered unless `header` is supplied. */
  title: string;
  header?: ReactNode;
  children: ReactNode;
  width?: number;
  className?: string;
}

/**
 * Right-side modal sheet. Esc or backdrop closes; focus moves into the sheet
 * and returns to the opener; body scroll is locked while open.
 */
export function Sheet({ open, onClose, title, header, children, width = 580, className }: SheetProps) {
  const mounted = useMounted();
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      } else if (e.key === "Tab" && panelRef.current) {
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prevOverflow;
      opener?.focus?.();
    };
  }, [open]);

  if (!mounted || !open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60]">
      <div className="absolute inset-0 animate-fade-in bg-ink/25" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={clsx(
          "absolute inset-y-0 right-0 flex w-full animate-sheet flex-col border-l border-ink bg-paper",
          className,
        )}
        style={{ maxWidth: width }}
      >
        <div className="flex items-start justify-between gap-4 border-b border-rule px-7 pb-4 pt-6">
          <div className="min-w-0" id={titleId}>
            {header ?? <h2 className="font-serif text-[26px] leading-tight">{title}</h2>}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="label shrink-0 pt-1 text-ink-2 hover:text-ink"
            aria-label={`Close ${title}`}
          >
            Esc ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-7 py-6">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
