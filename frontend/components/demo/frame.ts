/**
 * Helpers for driving the real app inside the /demo frame (same origin): client-side routing through the
 * frame's own Next router, readiness waits, eased scrolling and a quiet burgundy mark on the element being
 * discussed. Nothing here changes app behaviour; it only navigates and scrolls like a presenter would.
 */

interface NextRouterLike {
  push: (href: string, options?: { scroll?: boolean }) => void;
}

type FrameWindow = Window & typeof globalThis & { next?: { router?: NextRouterLike } };

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function frameWindow(frame: HTMLIFrameElement | null): FrameWindow | null {
  try {
    const w = frame?.contentWindow as FrameWindow | null | undefined;
    // Touching location throws if the frame is ever cross-origin (it never should be).
    return w && w.location && w.document ? w : null;
  } catch {
    return null;
  }
}

export function framePath(frame: HTMLIFrameElement | null): string | null {
  const w = frameWindow(frame);
  if (!w || w.location.href === "about:blank") return null;
  return w.location.pathname + w.location.search;
}

export function query(frame: HTMLIFrameElement | null, selector: string | null): HTMLElement | null {
  if (!selector) return null;
  const w = frameWindow(frame);
  try {
    return (w?.document.querySelector(selector) as HTMLElement | null) ?? null;
  } catch {
    return null;
  }
}

/** Navigate the frame with the app's own client router (no reload); full load as a fallback. */
export function route(frame: HTMLIFrameElement | null, url: string): void {
  const w = frameWindow(frame);
  if (!w) {
    if (frame) frame.src = url;
    return;
  }
  const router = w.next?.router;
  if (router && typeof router.push === "function") router.push(url, { scroll: true });
  else w.location.assign(url);
}

/** Poll until `check` is true (or timeout / cancel). Resolves to whether it became true. */
export async function waitFor(check: () => boolean, { timeout = 20000, interval = 80, cancelled }: { timeout?: number; interval?: number; cancelled?: () => boolean } = {}): Promise<boolean> {
  const end = Date.now() + timeout;
  for (;;) {
    if (cancelled?.()) return false;
    let ok = false;
    try {
      ok = check();
    } catch {
      ok = false;
    }
    if (ok) return true;
    if (Date.now() > end) return false;
    await sleep(interval);
  }
}

/** True while the frame is still showing a route-level loading skeleton or a busy region. */
export function frameBusy(frame: HTMLIFrameElement | null): boolean {
  const w = frameWindow(frame);
  if (!w) return true;
  if (w.document.readyState !== "complete") return true;
  return Boolean(w.document.querySelector('main [aria-busy="true"]:not(#investigation [aria-busy])'));
}

/** Wait for the frame to be at `match` (pathname prefix) with `ready` rendered, fonts loaded, then settle a beat. */
export async function waitForPage(
  frame: HTMLIFrameElement | null,
  match: string | null,
  ready: string | null,
  opts: { timeout?: number; cancelled?: () => boolean } = {},
): Promise<boolean> {
  const ok = await waitFor(
    () => {
      const path = framePath(frame);
      if (!path) return false;
      if (match && !(match === "/" ? path === "/" || path.startsWith("/?") : path.startsWith(match))) return false;
      if (ready && !query(frame, ready)) return false;
      return !frameBusy(frame);
    },
    { timeout: opts.timeout ?? 30000, cancelled: opts.cancelled },
  );
  const w = frameWindow(frame);
  try {
    await Promise.race([w?.document.fonts?.ready, sleep(1500)]);
  } catch {
    /* fonts API unavailable */
  }
  await sleep(160);
  return ok;
}

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** Eased scroll of the frame document (or of an inner scroller) to `top`. */
export function animateScroll(frame: HTMLIFrameElement | null, top: number, ms?: number, scroller?: HTMLElement | null): Promise<void> {
  const w = frameWindow(frame);
  if (!w) return Promise.resolve();
  const el = scroller ?? null;
  const start = el ? el.scrollTop : w.scrollY;
  const max = el ? el.scrollHeight - el.clientHeight : w.document.documentElement.scrollHeight - w.innerHeight;
  const target = Math.max(0, Math.min(top, Math.max(0, max)));
  const dist = target - start;
  if (Math.abs(dist) < 2) return Promise.resolve();
  const duration = ms ?? Math.max(650, Math.min(1500, Math.abs(dist) * 0.7));
  return new Promise((resolve) => {
    const t0 = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / duration);
      const y = start + dist * ease(t);
      if (el) el.scrollTop = y;
      else w.scrollTo(0, y);
      if (t < 1) requestAnimationFrame(step);
      else resolve();
    };
    // The parent's frame clock: a framed document's rAF can be throttled by the browser.
    requestAnimationFrame(step);
  });
}

/** Scroll so `el` sits a little below the top of the frame (or stays put if it is already comfortably visible). */
export async function bringIntoView(frame: HTMLIFrameElement | null, el: HTMLElement, { offset = 40, force = false } = {}): Promise<void> {
  const w = frameWindow(frame);
  if (!w) return;
  const r = el.getBoundingClientRect();
  const visible = r.top >= offset * 0.5 && r.top < w.innerHeight * 0.55;
  if (visible && !force) return;
  await animateScroll(frame, w.scrollY + r.top - offset);
}

const STYLE_ID = "anti-demo-style";

/** Presentation-only CSS in the frame: the burgundy focus mark, and no scrollbars on stage. */
export function injectStyle(frame: HTMLIFrameElement | null): void {
  const w = frameWindow(frame);
  if (!w || w.document.getElementById(STYLE_ID)) return;
  const style = w.document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    html { scrollbar-width: none; }
    html::-webkit-scrollbar { display: none; }
    [data-demo-mark] { outline: 1px solid var(--color-accent, #6e1a1f) !important; outline-offset: 14px; transition: outline-color 700ms ease !important; }
    [data-demo-mark="out"] { outline-color: transparent !important; }
  `;
  w.document.head.appendChild(style);
}

/** Draw the mark on `el`, then fade it after `hold` ms. Returns a cleanup. */
export function mark(frame: HTMLIFrameElement | null, el: HTMLElement | null, hold = 2600): () => void {
  if (!el) return () => undefined;
  injectStyle(frame);
  el.setAttribute("data-demo-mark", "in");
  const t1 = setTimeout(() => el.setAttribute("data-demo-mark", "out"), hold);
  const t2 = setTimeout(() => el.removeAttribute("data-demo-mark"), hold + 800);
  return () => {
    clearTimeout(t1);
    clearTimeout(t2);
    el.removeAttribute("data-demo-mark");
  };
}

export function clearMarks(frame: HTMLIFrameElement | null): void {
  const w = frameWindow(frame);
  w?.document.querySelectorAll("[data-demo-mark]").forEach((e) => e.removeAttribute("data-demo-mark"));
}

/** Nearest scrollable ancestor-or-self of `el` inside the frame (for the investigation panel's inner pane). */
export function scrollerOf(el: HTMLElement | null): HTMLElement | null {
  let cur: HTMLElement | null = el;
  while (cur) {
    const s = cur.ownerDocument.defaultView?.getComputedStyle(cur);
    if (s && /(auto|scroll)/.test(s.overflowY) && cur.scrollHeight > cur.clientHeight + 4) return cur;
    cur = cur.parentElement;
  }
  return null;
}
