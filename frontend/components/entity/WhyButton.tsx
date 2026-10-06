"use client";

import clsx from "clsx";
import { useState } from "react";
import { useCommand } from "@/components/KeyboardProvider";
import { WhyDrawer } from "@/components/WhyDrawer";
import type { AntiScore } from "@/lib/types";

/** "Why 71?" — opens the full score decomposition. Also bound to Y and the palette. */
export function WhyButton({
  score,
  entityName,
  scoreLabel,
  className,
  isDemo = false,
}: {
  score: AntiScore | null;
  entityName: string;
  scoreLabel: string;
  className?: string;
  isDemo?: boolean;
}) {
  const [open, setOpen] = useState(false);
  useCommand({
    id: `why-${entityName}`,
    label: `Why ${score?.total ?? "this score"}? — ${entityName}`,
    hint: "Y",
    keywords: "score explain breakdown",
    run: () => setOpen(true),
  });
  return (
    <>
      <button
        type="button"
        data-action="why"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className={clsx(
          "group inline-flex items-baseline gap-2 border-b border-ink pb-[2px] font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:border-accent hover:text-accent",
          className,
        )}
      >
        Why {score ? score.total : "this score"}?
        <span aria-hidden className="transition-transform group-hover:translate-x-0.5">→</span>
      </button>
      <WhyDrawer open={open} onClose={() => setOpen(false)} score={score} entityName={entityName} scoreLabel={scoreLabel} isDemo={isDemo} />
    </>
  );
}
