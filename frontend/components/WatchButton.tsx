"use client";

import clsx from "clsx";
import { useState } from "react";
import { addWatch, removeWatch } from "@/lib/api";
import type { WatchRequest } from "@/lib/types";
import { useToast } from "./Toast";
import { useStaticSnapshot } from "@/components/StaticSnapshot";

export interface WatchButtonProps {
  entityType: WatchRequest["entity_type"];
  entityId: string;
  watched: boolean;
  /** Watchlist item id, if known (needed for DELETE /api/watchlist/{item_id}). */
  itemId?: string | null;
  /** Override persistence; otherwise the watchlist API is called. */
  onToggle?: (next: boolean) => void | Promise<void>;
  /** Called after a successful change. */
  onChange?: (next: boolean) => void;
  size?: "sm" | "md";
  className?: string;
}

/**
 * Optimistic watch toggle. Flips immediately, persists, and reverts with a
 * toast if the request fails. Carries data-action="watch" for the W shortcut.
 */
export function WatchButton({ entityType, entityId, watched, itemId, onToggle, onChange, size = "sm", className }: WatchButtonProps) {
  const staticSnapshot = useStaticSnapshot();
  const [on, setOn] = useState(watched);
  const [item, setItem] = useState<string | null>(itemId ?? null);
  const [pending, setPending] = useState(false);
  const toast = useToast();

  const toggle = async () => {
    if (pending) return;
    const next = !on;
    setOn(next);
    setPending(true);
    try {
      if (onToggle) {
        await onToggle(next);
      } else if (next) {
        const created = await addWatch({ entity_type: entityType, entity_id: entityId });
        setItem(created?.id ?? null);
      } else {
        await removeWatch(item ?? entityId);
        setItem(null);
      }
      onChange?.(next);
      toast(next ? "Added to watchlist" : "Removed from watchlist");
    } catch {
      setOn(!next);
      toast("Could not update the watchlist", { tone: "error" });
    } finally {
      setPending(false);
    }
  };

  if (staticSnapshot) return null; // nothing to write to in the verified snapshot
  return (
    <button
      type="button"
      data-action="watch"
      aria-pressed={on}
      aria-busy={pending}
      onClick={toggle}
      className={clsx(
        "inline-flex items-center gap-1.5 font-mono uppercase tracking-[0.08em] transition-colors",
        size === "sm" ? "text-[10.5px]" : "border border-ink px-3 py-1.5 text-[11px] hover:bg-ink hover:text-paper",
        on ? "text-accent" : "text-ink-2 hover:text-ink",
        size === "md" && on && "border-accent hover:bg-accent",
        className,
      )}
    >
      <span aria-hidden>{on ? "●" : "+"}</span>
      {on ? "Watching" : "Watch"}
    </button>
  );
}
