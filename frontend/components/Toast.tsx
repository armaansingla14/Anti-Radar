"use client";

import clsx from "clsx";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export type ToastTone = "neutral" | "error";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

export type ToastFn = (message: string, opts?: { tone?: ToastTone; durationMs?: number }) => void;

const ToastContext = createContext<ToastFn | null>(null);

/** Access the toast function. Outside a provider it is a no-op. */
export function useToast(): ToastFn {
  return useContext(ToastContext) ?? noop;
}

const noop: ToastFn = () => {};

/** Lightweight toasts, bottom-left, mono, auto-dismiss. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((t) => t.id !== id)), []);

  const toast = useCallback<ToastFn>(
    (message, opts) => {
      const id = nextId.current++;
      setItems((xs) => [...xs.slice(-3), { id, message, tone: opts?.tone ?? "neutral" }]);
      window.setTimeout(() => dismiss(id), opts?.durationMs ?? 3200);
    },
    [dismiss],
  );

  const value = useMemo(() => toast, [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-5 left-5 z-[70] flex flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={clsx(
              "pointer-events-auto flex animate-rise items-center gap-3 rounded-sm border bg-paper px-3 py-2 font-mono text-[11px] tracking-[0.02em] rule-b",
              t.tone === "error" ? "border-accent text-accent" : "border-ink text-ink",
            )}
          >
            <span aria-hidden>{t.tone === "error" ? "×" : "●"}</span>
            <span>{t.message}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="ml-2 text-ink-3 hover:text-ink"
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
