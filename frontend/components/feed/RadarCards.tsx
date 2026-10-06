"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { IntelCard } from "@/components/IntelCard";
import { useCommand } from "@/components/KeyboardProvider";
import { useToast } from "@/components/Toast";
import { postFeedback } from "@/lib/api";
import type { IntelCard as IntelCardData } from "@/lib/types";
import { THEMES } from "./prose";

export interface RadarCardsProps {
  cards: IntelCardData[];
  /** Shown when the list is empty (filter-specific copy). */
  emptyTitle: string;
  emptyBody: string;
}

/**
 * The numbered card column. Owns optimistic dismissal: the card disappears
 * immediately, POST /api/feedback {label: "dismiss"} persists it, and the card
 * is restored in place if the request fails.
 */
export function RadarCards({ cards: initial, emptyTitle, emptyBody }: RadarCardsProps) {
  const [cards, setCards] = useState(initial);
  const [source, setSource] = useState(initial);
  const toast = useToast();

  // Reset when the server sends a new list (filter change, refresh).
  if (source !== initial) {
    setSource(initial);
    setCards(initial);
  }

  const dismiss = async (card: IntelCardData) => {
    const index = cards.findIndex((c) => c.id === card.id);
    setCards((cs) => cs.filter((c) => c.id !== card.id));
    try {
      await postFeedback({ entity_type: card.entity_type, entity_id: card.id, label: "dismiss" });
      toast(`Dismissed ${card.name} — it will not resurface on the radar`);
    } catch {
      setCards((cs) => (cs.some((c) => c.id === card.id) ? cs : [...cs.slice(0, index), card, ...cs.slice(index)]));
      toast(`Could not dismiss ${card.name}`, { tone: "error" });
    }
  };

  if (cards.length === 0) {
    return <EmptyState title={emptyTitle}>{emptyBody}</EmptyState>;
  }

  return (
    <div className="space-y-10">
      {cards.map((card, i) => (
        <IntelCard key={card.id} card={{ ...card, rank: i + 1 }} onDismiss={dismiss} />
      ))}
    </div>
  );
}

function ThemeCommand({ themeKey, label }: { themeKey: string; label: string }) {
  const router = useRouter();
  useCommand({
    id: `radar-theme-${themeKey}`,
    label: `Radar: filter to ${label}`,
    keywords: `theme thesis ${themeKey}`,
    run: () => router.push(`/?theme=${themeKey}`),
  });
  return null;
}

/** Page-level ⌘K commands for the Radar feed. */
export function RadarCommands() {
  const router = useRouter();
  useCommand({
    id: "radar-unknown-yesterday",
    label: "Show Unknown Yesterday",
    hint: "New in 7 days",
    keywords: "new only fresh first seen radar filter",
    run: () => router.push("/?new=1"),
  });
  useCommand({
    id: "radar-all",
    label: "Radar: show everything",
    keywords: "clear filters all reset",
    run: () => router.push("/"),
  });
  useCommand({
    id: "radar-top-story",
    label: "Open today's top story",
    keywords: "first card lead",
    run: () => {
      const href = document.querySelector<HTMLElement>("[data-card]")?.dataset.href;
      if (href) router.push(href);
    },
  });
  return (
    <>
      {THEMES.map((t) => (
        <ThemeCommand key={t.key} themeKey={t.key} label={t.label} />
      ))}
    </>
  );
}
