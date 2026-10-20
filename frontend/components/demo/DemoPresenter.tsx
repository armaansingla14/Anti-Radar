"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FinalCard, TitleCard } from "./Cards";
import {
  animateScroll,
  bringIntoView,
  clearMarks,
  framePath,
  frameWindow,
  injectStyle,
  mark,
  query,
  route,
  scrollerOf,
  sleep,
  waitFor,
  waitForPage,
} from "./frame";
import type { DemoPlan, DemoStep } from "./plan";
import { BAR_HEIGHT, PresenterBar } from "./PresenterBar";
import { warmAndPlan, type WarmStatus } from "./warm";

/** Automation hooks for the Playwright tour / screenshot scripts (read-only view + the presenter's own moves). */
export interface AntiDemoApi {
  plan: DemoPlan | null;
  state: () => { index: number; scene: number; settled: boolean; path: string | null; total: number };
  next: () => void;
  prev: () => void;
  go: (index: number) => void;
  /** Eased scroll of the stage by `dy` px over `ms` (inside `selector`'s scroller when given). */
  scroll: (dy: number, ms?: number, selector?: string) => Promise<void>;
}

declare global {
  interface Window {
    __antiDemo?: AntiDemoApi;
  }
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return !["checkbox", "radio", "button", "submit", "reset", "range"].includes((el as HTMLInputElement).type);
  return false;
}

const samePath = (a: string | null, b: string | null) => !!a && !!b && a.split("#")[0] === b.split("#")[0];
const matchOf = (url: string) => url.split("?")[0];

/**
 * /demo — a guided, presenter-friendly walk through the real app. A title card warms the backend and
 * resolves the scenes from live data; the stage is the real app in a same-origin frame, driven with the
 * app's own router; a slim bar underneath carries scene N/10, ← → and Exit.
 */
export function DemoPresenter() {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [plan, setPlan] = useState<DemoPlan | null>(null);
  const planRef = useRef<DemoPlan | null>(null);
  const [warm, setWarm] = useState<WarmStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [index, setIndex] = useState(0);
  const indexRef = useRef(0);
  const tokenRef = useRef(0);
  const [settled, setSettled] = useState(true);
  const [barHidden, setBarHidden] = useState(false);
  const [veil, setVeil] = useState(false);
  const startScene = useRef<number | null>(null);

  // The stage owns the viewport while the demo runs.
  useEffect(() => {
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    const n = Number(new URLSearchParams(window.location.search).get("scene"));
    startScene.current = Number.isInteger(n) && n >= 1 && n <= 10 ? n : null;
    return () => {
      html.style.overflow = prev;
    };
  }, []);

  // Pre-flight: demo dataset cookie → wake backend → resolve scenes → warm pages.
  useEffect(() => {
    const ctl = new AbortController();
    warmAndPlan(setWarm, ctl.signal)
      .then((p) => {
        planRef.current = p;
        setPlan(p);
      })
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return;
        setError(err instanceof Error && err.message !== "network" ? err.message : "The backend could not be reached. It may still be starting.");
      });
    return () => ctl.abort();
  }, [attempt]);

  const navigate = useCallback(async (url: string, ready: string | null, cancelled: () => boolean) => {
    const frame = frameRef.current;
    if (samePath(framePath(frame), url)) {
      await waitForPage(frame, matchOf(url), ready, { cancelled, timeout: 15000 });
      return;
    }
    setVeil(true);
    await sleep(220);
    if (cancelled()) return;
    route(frame, url);
    await waitForPage(frame, matchOf(url), ready, { cancelled, timeout: 45000 });
  }, []);

  const go = useCallback(
    async (target: number) => {
      const p = planRef.current;
      const frame = frameRef.current;
      if (!p || !frame) return;
      const to = Math.max(0, Math.min(p.steps.length - 1, target));
      const from = indexRef.current;
      const token = ++tokenRef.current;
      const cancelled = () => token !== tokenRef.current;
      indexRef.current = to;
      setIndex(to);
      setSettled(false);
      clearMarks(frame);
      const step: DemoStep = p.steps[to];

      if (!step.url && !step.click) {
        setVeil(false);
        await sleep(700);
        if (!cancelled()) setSettled(true);
        return;
      }

      const forward = to === from + 1;
      const before = framePath(frame);
      // Show where the next page comes from (e.g. the Investigate button behind an existing Anti Brief).
      const cue = forward ? query(frame, step.cue ?? null) : null;
      if (cue) {
        await bringIntoView(frame, cue, { offset: 160 });
        mark(frame, cue, 1300);
        await sleep(1500);
        if (cancelled()) return;
      }
      const investigation = step.settle === "investigation";
      const needClick = !!step.click && (investigation || !step.url || (forward && !samePath(before, step.url)));
      let navigated = false;

      if (needClick && step.click) {
        let el = query(frame, step.click);
        if (!el) {
          // Not on the page that holds the action: open it first (the scene's list page, or the entity page).
          const host = investigation ? step.url : p.steps.find((s) => s.scene === step.scene && s.url && !s.click)?.url ?? null;
          if (host) await navigate(host, step.click, cancelled);
          setVeil(false);
          el = query(frame, step.click);
        }
        if (cancelled()) return;
        const alreadyOpen = investigation && !!query(frame, "#investigation");
        if (el && !alreadyOpen) {
          await bringIntoView(frame, el, { offset: 160 });
          const unmark = mark(frame, el, 1100);
          await sleep(900);
          if (cancelled()) return unmark();
          const leaves = !!step.match && !(framePath(frame) ?? "").startsWith(step.match);
          if (leaves) setVeil(true);
          el.click();
          const ok = await waitForPage(frame, step.match, step.ready, { cancelled, timeout: 60000 });
          if (cancelled()) return;
          if (!ok && step.url) await navigate(step.url, step.ready, cancelled);
          navigated = leaves;
        } else if (!alreadyOpen && step.url) {
          await navigate(step.url, step.ready, cancelled);
          navigated = true;
        }
        // A brief created by the click now has a URL: remember it so ← / → return without regenerating.
        const now = framePath(frame);
        if (!step.url && step.match && now?.startsWith(step.match)) {
          const steps = p.steps.map((s, i) => (i === to ? { ...s, url: now } : s));
          planRef.current = { ...p, steps };
          setPlan(planRef.current);
        }
      } else if (step.url) {
        navigated = !samePath(before, step.url);
        await navigate(step.url, step.ready, cancelled);
      }
      if (cancelled()) return;
      // The page rendered an error (e.g. the backend restarted or is still waking): reload it once, fully.
      const reloadUrl = planRef.current?.steps[to]?.url ?? step.url;
      if (step.ready && !query(frame, step.ready) && reloadUrl) {
        setVeil(true);
        frame.src = reloadUrl;
        await sleep(400);
        await waitForPage(frame, matchOf(reloadUrl), step.ready, { cancelled, timeout: 90000 });
        if (cancelled()) return;
        navigated = true;
      }
      setVeil(false);
      if (navigated) await sleep(380);

      const focus = query(frame, step.focus);
      if (focus && navigated && step.hold_top) {
        await sleep(step.hold_top);
        if (cancelled()) return;
      }
      if (focus) {
        await bringIntoView(frame, focus, { offset: step.focus_offset ?? 40 });
        if (cancelled()) return;
        mark(frame, focus, 2800);
      } else if (!navigated && !needClick) {
        await animateScroll(frame, 0);
      }
      if (investigation) {
        await waitFor(() => !!query(frame, "#investigation article"), { timeout: 150000, cancelled });
        await sleep(400);
      }
      if (!cancelled()) setSettled(true);
    },
    [navigate],
  );

  const next = useCallback(() => {
    const p = planRef.current;
    if (!p) return;
    if (indexRef.current < p.steps.length - 1) void go(indexRef.current + 1);
  }, [go]);
  const prev = useCallback(() => {
    if (indexRef.current > 0) void go(indexRef.current - 1);
  }, [go]);

  const exit = useCallback(() => {
    const path = framePath(frameRef.current);
    window.location.assign(path && indexRef.current > 0 ? path : "/");
  }, []);

  const ready = !!plan && frameLoaded;

  const start = useCallback(() => {
    if (ready) void go(1);
  }, [ready, go]);

  // Keyboard, in the page and inside the stage: → / PageDown next, ← / PageUp back, H hide the bar.
  const onKey = useRef<(e: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    onKey.current = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      const k = e.key;
      if (indexRef.current === 0 && (k === "Enter" || k === "ArrowRight" || k === "PageDown")) {
        e.preventDefault();
        e.stopPropagation();
        start();
        return;
      }
      if (k === "ArrowRight" || k === "PageDown") {
        e.preventDefault();
        e.stopPropagation();
        next();
      } else if (k === "ArrowLeft" || k === "PageUp") {
        e.preventDefault();
        e.stopPropagation();
        prev();
      } else if (k === "h" || k === "H") {
        e.preventDefault();
        e.stopPropagation();
        setBarHidden((h) => !h);
      }
    };
  }, [next, prev, start]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => onKey.current(e);
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, []);

  const onFrameLoad = useCallback(() => {
    const frame = frameRef.current;
    const w = frameWindow(frame);
    if (!w || w.location.href === "about:blank") return;
    injectStyle(frame);
    // Capture phase so the app's own shortcuts never see the presenter's keys.
    w.addEventListener("keydown", (e) => onKey.current(e), true);
    setFrameLoaded(true);
  }, []);

  // Jump straight to ?scene=N once everything is ready (presenter recovery, screenshots).
  useEffect(() => {
    if (!ready || startScene.current === null) return;
    const scene = startScene.current;
    startScene.current = null;
    const i = planRef.current?.steps.findIndex((s) => s.scene === scene) ?? -1;
    if (i > 0) void go(i);
  }, [ready, go]);

  // Automation surface.
  useEffect(() => {
    window.__antiDemo = {
      plan: planRef.current,
      state: () => ({
        index: indexRef.current,
        scene: planRef.current?.steps[indexRef.current]?.scene ?? 1,
        settled: document.querySelector('[data-demo="demo-root"]')?.getAttribute("data-demo-ready") === "true",
        path: framePath(frameRef.current),
        total: planRef.current?.steps.length ?? 0,
      }),
      next,
      prev,
      go: (i: number) => void go(i),
      scroll: async (dy: number, ms?: number, selector?: string) => {
        const frame = frameRef.current;
        const w = frameWindow(frame);
        if (!w) return;
        const scroller = selector ? scrollerOf(query(frame, selector)) : null;
        const from = scroller ? scroller.scrollTop : w.scrollY;
        await animateScroll(frame, from + dy, ms, scroller);
      },
    };
    return () => {
      delete window.__antiDemo;
    };
  }, [plan, next, prev, go]);

  const step = plan?.steps[index] ?? null;
  const isTitle = index === 0;
  const isFinal = step?.scene_key === "final";
  const barVisible = !!step && !isTitle && !isFinal && !barHidden;

  return (
    <div
      data-demo="demo-root"
      data-demo-phase={error ? "error" : ready ? "ready" : "warming"}
      data-demo-step={index}
      data-demo-scene={step?.scene ?? 1}
      data-demo-ready={String(settled && (index > 0 || ready))}
      className="fixed inset-0 z-[90] overflow-hidden bg-paper text-ink"
    >
      <style>{`
        @keyframes anti-demo-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        .anti-demo-rise { animation: anti-demo-rise 900ms cubic-bezier(.2,.7,.2,1) both; }
        @keyframes anti-demo-scan { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
        .anti-demo-scan { animation: anti-demo-scan 1.6s cubic-bezier(.6,0,.4,1) infinite; }
        @keyframes anti-demo-pulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
        .anti-demo-pulse { animation: anti-demo-pulse 1.2s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .anti-demo-rise, .anti-demo-scan, .anti-demo-pulse { animation: none; } }
      `}</style>

      {plan && (
        <div
          className="absolute inset-x-0 top-0 transition-[bottom] duration-300 ease-out"
          style={{ bottom: barVisible ? BAR_HEIGHT : 0 }}
        >
          <iframe
            ref={frameRef}
            data-demo="demo-stage"
            src="/"
            title="Anti Radar"
            onLoad={onFrameLoad}
            className="block h-full w-full border-0 bg-paper"
          />
          <div
            aria-hidden
            className={`pointer-events-none absolute inset-0 bg-paper transition-opacity ease-out ${veil ? "opacity-100 duration-200" : "opacity-0 duration-500"}`}
          />
        </div>
      )}

      <TitleCard
        visible={isTitle}
        ready={ready}
        status={warm}
        error={error}
        asOf={plan?.as_of ?? null}
        isSnapshot={plan?.is_snapshot ?? false}
        onStart={start}
        onRetry={() => {
          setError(null);
          setWarm(null);
          setAttempt((n) => n + 1);
        }}
      />
      <FinalCard
        visible={isFinal}
        asOf={plan?.as_of ?? null}
        isSnapshot={plan?.is_snapshot ?? false}
        formations={plan?.counts.formations ?? 0}
        people={plan?.counts.people ?? 0}
        onReplay={() => void go(0)}
        onExit={exit}
      />
      {step && (
        <PresenterBar
          step={step}
          hidden={!barVisible}
          settled={settled}
          canPrev={index > 0}
          canNext={!!plan && index < plan.steps.length - 1}
          onPrev={prev}
          onNext={next}
          onExit={exit}
        />
      )}
    </div>
  );
}
