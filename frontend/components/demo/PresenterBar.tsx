"use client";

import clsx from "clsx";
import { SCENE_COUNT, type DemoStep } from "./plan";

export const BAR_HEIGHT = 56;

const pad2 = (n: number) => String(n).padStart(2, "0");

const key =
  "inline-flex h-8 w-8 items-center justify-center border font-mono text-[13px] transition-colors disabled:cursor-default disabled:opacity-30";

/**
 * Slim presenter strip under the stage (never over it): scene N/10, the scene name, one line of context
 * built from the data, ← → and Exit. A burgundy hairline on its top edge shows progress. H hides it.
 */
export function PresenterBar({
  step,
  hidden,
  settled,
  canPrev,
  canNext,
  onPrev,
  onNext,
  onExit,
}: {
  step: DemoStep;
  hidden: boolean;
  settled: boolean;
  canPrev: boolean;
  canNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  onExit: () => void;
}) {
  const progress = Math.min(1, step.scene / SCENE_COUNT);
  return (
    <div
      data-demo="demo-bar"
      aria-hidden={hidden}
      style={{ height: BAR_HEIGHT }}
      className={clsx(
        "absolute inset-x-0 bottom-0 z-30 border-t border-ink bg-paper transition-[transform,opacity] duration-300 ease-out",
        hidden ? "pointer-events-none translate-y-full opacity-0" : "translate-y-0 opacity-100",
      )}
    >
      <span
        aria-hidden
        className="absolute -top-px left-0 h-[2px] bg-accent transition-[width] duration-700 ease-out"
        style={{ width: `${progress * 100}%` }}
      />
      <div className="flex h-full items-center gap-5 px-8">
        <span className="shrink-0 font-serif text-[15px] leading-none tracking-[0.06em] text-ink">
          ANTI <span className="text-accent">RADAR</span>
        </span>
        <span aria-hidden className="h-5 w-px shrink-0 bg-rule-strong" />
        <span data-demo="demo-counter" className="tnum shrink-0 font-mono text-[11.5px] tracking-[0.08em] text-ink">
          {pad2(step.scene)}
          <span className="text-ink-3"> / {SCENE_COUNT}</span>
        </span>
        <span data-demo="demo-scene-title" className="shrink-0 font-serif text-[19px] italic leading-none text-ink">
          {step.title}
        </span>
        <span
          data-demo="demo-caption"
          className="min-w-0 flex-1 truncate text-[13px] leading-none text-ink-2"
          title={step.caption}
        >
          {step.caption}
        </span>
        <span
          aria-hidden
          className={clsx("h-1.5 w-1.5 shrink-0 bg-accent transition-opacity duration-300", settled ? "opacity-0" : "anti-demo-pulse")}
        />
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            data-demo="demo-prev"
            onClick={onPrev}
            disabled={!canPrev}
            aria-label="Previous scene"
            className={clsx(key, "border-rule-strong text-ink-2 hover:border-ink hover:text-ink")}
          >
            ←
          </button>
          <button
            type="button"
            data-demo="demo-next"
            onClick={onNext}
            disabled={!canNext}
            aria-label="Next scene"
            className={clsx(key, "border-ink text-ink hover:bg-ink hover:text-paper")}
          >
            →
          </button>
        </div>
        <span className="hidden shrink-0 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink-3 xl:inline">
          <kbd className="text-ink-2">H</kbd> hide
        </span>
        <button
          type="button"
          data-demo="demo-exit"
          onClick={onExit}
          className="shrink-0 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink-2 underline decoration-rule-strong underline-offset-4 transition-colors hover:text-accent hover:decoration-accent"
        >
          Exit demo
        </button>
      </div>
    </div>
  );
}
