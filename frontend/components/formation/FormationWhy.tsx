"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { WhyDrawer } from "@/components/WhyDrawer";
import { getFormationScore } from "@/lib/api";
import type { AntiScore, FormationCard } from "@/lib/types";
import { toAntiScore } from "./logic";

/**
 * "Why 74?" for a formation candidate. Opens the shared WhyDrawer and loads
 * the deterministic breakdown from /api/formation/{id}/score on first open.
 * If that call fails, the components already on the card (same source) are shown.
 */
export function FormationWhy({ card, className }: { card: FormationCard; className?: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ score: AntiScore | null; error: string | null } | null>(null);

  useEffect(() => {
    if (!open || state) return;
    let cancelled = false;
    getFormationScore(card.id)
      .then((raw) => !cancelled && setState({ score: toAntiScore(raw, card), error: null }))
      .catch(() => {
        if (cancelled) return;
        const fallback = toAntiScore(null, card);
        setState({ score: fallback, error: fallback ? null : "Could not load the score breakdown." });
      });
    return () => {
      cancelled = true;
    };
  }, [open, state, card]);

  return (
    <>
      <button
        type="button"
        data-action="why"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className={clsx(
          "group inline-flex items-baseline gap-1.5 border-b border-rule-strong pb-[1px] font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-accent hover:text-accent",
          className,
        )}
      >
        Why {card.score}?
        <span aria-hidden className="transition-transform group-hover:translate-x-0.5">→</span>
      </button>
      <WhyDrawer
        open={open}
        onClose={() => setOpen(false)}
        score={state?.score ?? null}
        error={state?.error ?? null}
        loading={open && !state}
        entityName={card.title}
        scoreLabel="Formation Score"
        isDemo={card.is_demo}
      />
    </>
  );
}
