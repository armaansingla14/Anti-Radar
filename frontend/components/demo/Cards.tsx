"use client";

import clsx from "clsx";
import type { CSSProperties, ReactNode } from "react";
import { fmtDay } from "./plan";
import type { WarmStatus } from "./warm";

/** Slow, quiet entrance shared by the full-bleed cards (keyframes live in DemoPresenter's style tag). */
function Rise({ delay, children, className }: { delay: number; children: ReactNode; className?: string }) {
  const style: CSSProperties = { animationDelay: `${delay}ms` };
  return (
    <div className="anti-demo-rise" style={style}>
      <div className={className}>{children}</div>
    </div>
  );
}

function DataLine({ asOf, isSnapshot }: { asOf: string | null; isSnapshot: boolean }) {
  const date = fmtDay(asOf);
  if (!date) return <span className="text-ink-3">—</span>;
  return (
    <span>
      <span className="text-ink-3">{isSnapshot ? "Verified snapshot" : "Live data"} · as of </span>
      <span className="tnum text-ink">{date}</span>
    </span>
  );
}

function statusText(s: WarmStatus | null, error: string | null): string {
  if (error) return "The radar did not answer.";
  if (!s) return "Preparing the radar…";
  if (s.phase === "waking") return "Waking the radar…";
  if (s.phase === "warming-pages") return "Opening the pages…";
  return "Preparing the radar…";
}

export function TitleCard({
  visible,
  ready,
  status,
  error,
  asOf,
  isSnapshot,
  onStart,
  onRetry,
}: {
  visible: boolean;
  ready: boolean;
  status: WarmStatus | null;
  error: string | null;
  asOf: string | null;
  isSnapshot: boolean;
  onStart: () => void;
  onRetry: () => void;
}) {
  return (
    <section
      data-demo="demo-title"
      aria-hidden={!visible}
      className={clsx(
        "absolute inset-0 z-20 flex flex-col bg-paper transition-opacity duration-700 ease-out",
        visible ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <div className="mx-auto flex w-full max-w-[1240px] flex-1 flex-col px-10">
        <header className="flex items-baseline justify-between border-b border-ink pb-3 pt-8 font-mono text-[11px] uppercase tracking-[0.1em]">
          <span className="text-ink-2">Anti Radar · Built for Anti Fund</span>
          <DataLine asOf={asOf} isSnapshot={isSnapshot} />
        </header>

        <div className="flex flex-1 flex-col justify-center pb-[6vh]">
          <Rise delay={120}>
            <h1 className="font-serif text-[clamp(72px,9.4vw,152px)] font-normal leading-[0.9] tracking-[0.045em] text-ink">
              ANTI <span className="text-accent">RADAR</span>
            </h1>
          </Rise>
          <Rise delay={420} className="mt-9 flex items-center gap-6">
            <span aria-hidden className="h-px w-16 bg-ink" />
            <p className="font-serif text-[clamp(30px,3.1vw,48px)] italic leading-tight text-ink">Conviction Before Consensus.</p>
          </Rise>
          <Rise delay={720}>
            <p className="ml-[88px] mt-5 max-w-[54ch] text-[18px] leading-relaxed text-ink-2">
              This isn&apos;t another startup database. Radar looks for technical change before a company becomes obvious.
            </p>
          </Rise>

          <Rise delay={1020} className="ml-[88px] mt-14 flex flex-wrap items-center gap-x-8 gap-y-3">
            {error ? (
              <button
                type="button"
                data-demo="demo-retry"
                onClick={onRetry}
                className="inline-flex items-center gap-3 border border-ink px-6 py-3 font-mono text-[12px] uppercase tracking-[0.14em] text-ink transition-colors hover:bg-ink hover:text-paper"
              >
                Try again
              </button>
            ) : (
              <button
                type="button"
                data-demo="demo-start"
                data-demo-state={ready ? "ready" : "waiting"}
                onClick={onStart}
                disabled={!ready}
                className={clsx(
                  "group inline-flex items-center gap-4 border px-7 py-3.5 font-mono text-[12px] uppercase tracking-[0.16em] transition-colors duration-300",
                  ready ? "border-ink bg-ink text-paper hover:border-accent hover:bg-accent" : "cursor-wait border-rule-strong text-ink-3",
                )}
              >
                Start
                <span aria-hidden className={clsx("transition-transform duration-300", ready && "group-hover:translate-x-1")}>
                  →
                </span>
              </button>
            )}
            <div className="min-w-[280px]" aria-live="polite">
              {ready && !error ? (
                <p className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-2">
                  Ready · ten scenes · about three minutes
                </p>
              ) : (
                <div>
                  <p className={clsx("font-mono text-[11px] uppercase tracking-[0.1em]", error ? "text-accent" : "text-ink-2")}>
                    {statusText(status, error)}
                  </p>
                  {!error && (
                    <div className="relative mt-2 h-px w-[220px] overflow-hidden bg-rule">
                      <span className="anti-demo-scan absolute inset-y-0 left-0 w-1/3 bg-ink" />
                    </div>
                  )}
                  {error && <p className="mt-1 text-[12.5px] text-ink-3">{error}</p>}
                </div>
              )}
            </div>
          </Rise>
        </div>

        <footer className="flex items-baseline justify-between border-t border-rule py-4 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink-3">
          <span>
            <kbd className="text-ink-2">←</kbd> <kbd className="text-ink-2">→</kbd> move · <kbd className="text-ink-2">H</kbd> hides the presenter bar
          </span>
          <span>Real pages · real data · every fact sourced</span>
        </footer>
      </div>
    </section>
  );
}

export function FinalCard({
  visible,
  asOf,
  isSnapshot,
  formations,
  people,
  onReplay,
  onExit,
}: {
  visible: boolean;
  asOf: string | null;
  isSnapshot: boolean;
  formations: number;
  people: number;
  onReplay: () => void;
  onExit: () => void;
}) {
  return (
    <section
      data-demo="demo-final"
      aria-hidden={!visible}
      className={clsx(
        "absolute inset-0 z-10 flex flex-col bg-paper transition-opacity duration-700 ease-out",
        visible ? "opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      {visible && (
        <div className="mx-auto flex w-full max-w-[1240px] flex-1 flex-col justify-center px-10 pb-[4vh]">
          <Rise delay={150}>
            <p className="label text-ink-2">
              Anti Radar · <DataLine asOf={asOf} isSnapshot={isSnapshot} />
            </p>
          </Rise>
          <Rise delay={350} className="mt-6 border-t border-ink pt-10">
            <h2 className="font-serif text-[clamp(56px,7.2vw,118px)] font-normal leading-[0.95] tracking-[0.03em] text-ink">
              CONVICTION
              <br />
              BEFORE <span className="text-accent">CONSENSUS.</span>
            </h2>
          </Rise>
          <Rise delay={750} className="mt-10 grid max-w-[980px] grid-cols-3 gap-10 border-t border-rule pt-5">
            <Stat n={formations} label="formation candidates on the radar today" />
            <Stat n={people} label="people worth a first conversation this week" />
            <Stat n={null} label="every fact carries its public source" glyph="§" />
          </Rise>
          <Rise delay={1050} className="mt-14 flex items-center gap-8">
            <button
              type="button"
              data-demo="demo-exit-final"
              onClick={onExit}
              className="inline-flex items-center gap-3 border border-ink bg-ink px-6 py-3 font-mono text-[11.5px] uppercase tracking-[0.14em] text-paper transition-colors hover:border-accent hover:bg-accent"
            >
              Explore the radar <span aria-hidden>→</span>
            </button>
            <button
              type="button"
              data-demo="demo-replay"
              onClick={onReplay}
              className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-2 transition-colors hover:text-ink"
            >
              ↺ Replay
            </button>
          </Rise>
        </div>
      )}
    </section>
  );
}

function Stat({ n, label, glyph }: { n: number | null; label: string; glyph?: string }) {
  return (
    <div>
      <p className="tnum font-serif text-[44px] leading-none text-ink">{n ?? glyph ?? "—"}</p>
      <p className="mt-2 max-w-[26ch] text-[13.5px] leading-snug text-ink-2">{label}</p>
    </div>
  );
}
