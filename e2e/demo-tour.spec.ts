/**
 * Walks /demo end to end through the real app, scene by scene, with stable data-demo selectors.
 *
 * - Every step must settle (data-demo-ready="true"), land on the URL the plan resolved, render its key
 *   element inside the stage frame, and produce no console errors / page errors.
 * - RECORD=1: deliberate pacing (DWELL below, scaled by DEMO_PACE, total ≈ 2:30), smooth in-page scrolls
 *   driven by the presenter's own scroll helper, no mouse movement except the one Start click; writes
 *   test-results/demo-tour-meta.json (video path + where the clean take starts) for record-demo.sh.
 * - Without RECORD the pauses shrink (DEMO_PACE defaults to 0.08) and the presenter controls (H, ←, →
 *   buttons) are exercised as well.
 *
 * Per-scene overrides: DEMO_DWELL="home=10,brief=18" (seconds before DEMO_PACE).
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const RECORD = process.env.RECORD === "1";
const PACE = Number(process.env.DEMO_PACE ?? (RECORD ? 1 : 0.08));

/** Seconds on each step after it settles. Keys: scene key, plus "-1" for a scene's second beat. */
const DWELL: Record<string, number> = {
  title: 6.5,
  home: 12,
  formation: 6.5,
  "formation-1": 8.5,
  collision: 10.5,
  "why-now": 10.5,
  person: 11,
  meet: 8,
  "meet-1": 13,
  attention: 11,
  brief: 14,
  final: 7,
};
for (const pair of (process.env.DEMO_DWELL ?? "").split(",").filter(Boolean)) {
  const [k, v] = pair.split("=");
  if (k && Number.isFinite(Number(v))) DWELL[k.trim()] = Number(v);
}

/** Slow scroll during the dwell: [pixels, milliseconds, optional scroller selector inside the stage]. */
const DRIFT: Record<string, [number, number, string?]> = {
  home: [430, 4200],
  "meet-1": [760, 5200],
  brief: [900, 6000],
};

interface Step {
  scene: number;
  scene_key: string;
  title: string;
  caption: string;
  url: string | null;
  match: string | null;
  ready: string | null;
  focus: string | null;
  settle?: string | null;
}
interface Plan {
  steps: Step[];
  picks: Record<string, unknown>;
}

declare global {
  interface Window {
    __antiDemo?: {
      plan: Plan | null;
      state: () => { index: number; scene: number; settled: boolean; path: string | null; total: number };
      scroll: (dy: number, ms?: number, selector?: string) => Promise<void>;
    };
  }
}

const ROOT = '[data-demo="demo-root"]';
const STAGE = '[data-demo="demo-stage"]';
const pad2 = (n: number) => String(n).padStart(2, "0");

async function settledAt(page: Page, index: number, timeout = 200_000) {
  await page.waitForFunction(
    ({ root, i }) => {
      const r = document.querySelector(root);
      return r?.getAttribute("data-demo-step") === String(i) && r?.getAttribute("data-demo-ready") === "true";
    },
    { root: ROOT, i: index },
    { timeout },
  );
}

async function dwell(page: Page, key: string) {
  const total = (DWELL[key] ?? 8) * PACE * 1000;
  const drift = RECORD || PACE >= 0.5 ? DRIFT[key] : undefined;
  if (!drift) {
    await page.waitForTimeout(total);
    return;
  }
  const [dy, ms, sel] = drift;
  const lead = Math.max(0, total * 0.4);
  await page.waitForTimeout(lead);
  await page.evaluate(([d, m, s]) => window.__antiDemo?.scroll(d as number, m as number, (s as string) || undefined), [dy, ms * PACE, sel ?? ""] as const);
  await page.waitForTimeout(Math.max(0, total - lead - ms * PACE));
}

test("demo tour: ten scenes over real data", async ({ page }, testInfo) => {
  const t0 = Date.now();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  const noErrors = (where: string) => expect(errors, `console/page errors at ${where}`).toEqual([]);
  // Seconds since t0 at which each beat is on screen ("in") and is left ("out"): lets a voiceover be laid
  // over the take scene by scene (see e2e/voiceover.py).
  const marks: { key: string; scene: number; in_s: number; out_s?: number }[] = [];
  const now = () => (Date.now() - t0) / 1000;

  // ── Scene 1: title card, pre-flight (dataset cookie, wake backend, resolve scenes, warm pages) ──
  await page.goto("/demo", { waitUntil: "domcontentloaded" });
  const title = page.locator('[data-demo="demo-title"]');
  await expect(title).toContainText("ANTI");
  await expect(title).toContainText("Conviction Before Consensus.");
  await expect(title).toContainText("Radar looks for technical change before a company becomes obvious.");
  await expect(page.locator('[data-demo="demo-start"][data-demo-state="ready"]')).toBeVisible({ timeout: 240_000 });

  let takeStart = 0;
  if (RECORD) {
    // The first visit warmed everything; reload so the take opens on the title card's entrance.
    takeStart = (Date.now() - t0) / 1000;
    await page.goto("/demo", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-demo="demo-start"][data-demo-state="ready"]')).toBeVisible({ timeout: 120_000 });
  }
  marks.push({ key: "title", scene: 1, in_s: now() });
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === "radar_dataset")?.value, "demo dataset cookie").toBe("demo");

  const plan = (await page.evaluate(() => window.__antiDemo?.plan ?? null)) as Plan | null;
  expect(plan, "plan resolved").not.toBeNull();
  const steps = plan!.steps;
  expect(steps[0].scene).toBe(1);
  expect(steps.at(-1)!.scene).toBe(10);
  expect(new Set(steps.map((s) => s.scene)).size).toBe(10);
  console.log(`[demo] picks ${JSON.stringify(plan!.picks)}`);
  noErrors("title");
  await page.waitForTimeout(DWELL.title * PACE * 1000);
  marks[0].out_s = now();

  // One deliberate click (Start); the mouse then rests off-stage so nothing in the app shows a hover state.
  await page.locator('[data-demo="demo-start"]').click();
  await page.mouse.move(4, 1076);

  const stage = page.frameLocator(STAGE);
  const beats = new Map<number, number>();
  for (let i = 1; i < steps.length; i++) {
    const step = steps[i];
    const beat = beats.get(step.scene) ?? 0;
    beats.set(step.scene, beat + 1);
    const key = beat ? `${step.scene_key}-${beat}` : step.scene_key;
    const t = Date.now();

    await settledAt(page, i);
    marks.push({ key, scene: step.scene, in_s: now() });
    const state = await page.evaluate(() => window.__antiDemo!.state());
    console.log(`[demo] ${pad2(step.scene)} ${key.padEnd(14)} settled ${((Date.now() - t) / 1000).toFixed(1)}s  ${state.path ?? "(card)"}`);

    if (step.scene_key === "final") {
      await expect(page.locator('[data-demo="demo-final"]')).toBeVisible();
      await expect(page.locator('[data-demo="demo-final"]')).toContainText("CONVICTION");
      await expect(page.locator('[data-demo="demo-final"]')).toContainText("CONSENSUS.");
    } else {
      await expect(page.locator('[data-demo="demo-counter"]')).toHaveText(`${pad2(step.scene)} / 10`);
      await expect(page.locator('[data-demo="demo-scene-title"]')).toHaveText(step.title);
      const fresh = (await page.evaluate(() => window.__antiDemo!.plan)) as Plan;
      const expected = fresh.steps[i].url ?? step.match;
      expect(state.path, `frame URL at scene ${step.scene}`).toBeTruthy();
      if (expected) expect(state.path!.split("?")[0].startsWith(expected.split("?")[0]), `${state.path} ~ ${expected}`).toBe(true);
      if (step.ready) await expect(stage.locator(step.ready).first()).toBeAttached();
      if (step.settle === "investigation") await expect(stage.locator("#investigation article")).toBeVisible();
    }
    noErrors(`scene ${step.scene} (${key})`);

    // Functional pass only: exercise the presenter controls without disturbing a recording.
    if (!RECORD && step.scene_key === "why-now") {
      await page.keyboard.press("h");
      await expect(page.locator('[data-demo="demo-bar"]')).toHaveAttribute("aria-hidden", "true");
      await expect.poll(() => page.locator(STAGE).evaluate((el) => el.getBoundingClientRect().height)).toBe(1080);
      await page.keyboard.press("h");
      await expect(page.locator('[data-demo="demo-bar"]')).toHaveAttribute("aria-hidden", "false");
      await page.locator('[data-demo="demo-prev"]').click();
      await settledAt(page, i - 1);
      await page.locator('[data-demo="demo-next"]').click();
      await settledAt(page, i);
      await page.mouse.move(4, 1076);
    }

    await dwell(page, key);
    marks.at(-1)!.out_s = now();
    if (i < steps.length - 1) await page.keyboard.press("ArrowRight");
  }
  noErrors("end");

  const seconds = (Date.now() - t0) / 1000;
  console.log(`[demo] tour finished in ${seconds.toFixed(1)}s (take ≈ ${(seconds - takeStart).toFixed(1)}s)`);
  if (RECORD) {
    const video = page.video();
    const meta = { video: video ? await video.path() : null, take_start_s: takeStart, total_s: seconds, marks };
    fs.writeFileSync(path.join(testInfo.project.outputDir, "demo-tour-meta.json"), JSON.stringify(meta, null, 2));
  }
});
