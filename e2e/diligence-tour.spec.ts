/**
 * Walks /demo/diligence end to end through the real app: nine scenes resolved from live data (the most recent
 * diligence workspace, its most testable unverified claim, underwriting, founder questions, then the agents'
 * metrics, a failure the evaluator caught and the regression case that pins it).
 *
 * - Every step must settle (data-demo-ready="true"), land on the URL the plan resolved, render its ready
 *   element inside the stage frame, and produce no console errors / page errors.
 * - RECORD=1: deliberate pacing (DWELL below, scaled by DEMO_PACE; take ≈ 2:00), smooth in-page scrolls,
 *   no mouse movement except Start; writes test-results/diligence-tour-meta.json for record-demo.sh
 *   (TOUR=diligence).
 * - Without RECORD the pauses shrink (DEMO_PACE defaults to 0.08) and ← / → / H are exercised.
 *
 * Per-scene overrides: DEMO_DWELL="claim=12,agents-1=10" (seconds before DEMO_PACE).
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const RECORD = process.env.RECORD === "1";
const PACE = Number(process.env.DEMO_PACE ?? (RECORD ? 1 : 0.08));
const SCENES = 9;

/** Seconds on each step after it settles. Keys: scene key, plus "-1", "-2" for a scene's later beats. */
const DWELL: Record<string, number> = {
  title: 6,
  radar: 8.5,
  workspace: 8.5,
  ledger: 9.5,
  claim: 10,
  "test-plan": 10,
  underwriting: 7.5,
  "underwriting-1": 7.5,
  questions: 8.5,
  agents: 7,
  "agents-1": 8.5,
  "agents-2": 6.5,
  final: 7,
};
for (const pair of (process.env.DEMO_DWELL ?? "").split(",").filter(Boolean)) {
  const [k, v] = pair.split("=");
  if (k && Number.isFinite(Number(v))) DWELL[k.trim()] = Number(v);
}

/** Slow scroll during the dwell: [pixels, milliseconds]. */
const DRIFT: Record<string, [number, number]> = {
  ledger: [640, 4200],
  claim: [420, 3600],
  "test-plan": [520, 4200],
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
  const [dy, ms] = drift;
  const lead = Math.max(0, total * 0.35);
  await page.waitForTimeout(lead);
  await page.evaluate(([d, m]) => window.__antiDemo?.scroll(d, m), [dy, ms * PACE] as const);
  await page.waitForTimeout(Math.max(0, total - lead - ms * PACE));
}

test("diligence tour: nine scenes over real data", async ({ page }, testInfo) => {
  const t0 = Date.now();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  const noErrors = (where: string) => expect(errors, `console/page errors at ${where}`).toEqual([]);
  const marks: { key: string; scene: number; caption: string; in_s: number; out_s?: number }[] = [];
  const now = () => (Date.now() - t0) / 1000;

  // ── Title card: pre-flight (dataset cookie, wake backend, resolve scenes, warm pages) ──
  await page.goto("/demo/diligence", { waitUntil: "domcontentloaded" });
  const title = page.locator('[data-demo="demo-title"]');
  await expect(title).toContainText("ANTI");
  await expect(title).toContainText("Technical Diligence.");
  await expect(page.locator('[data-demo="demo-start"][data-demo-state="ready"]')).toBeVisible({ timeout: 240_000 });

  let takeStart = 0;
  if (RECORD) {
    takeStart = now();
    await page.goto("/demo/diligence", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-demo="demo-start"][data-demo-state="ready"]')).toBeVisible({ timeout: 120_000 });
  }
  marks.push({ key: "title", scene: 0, caption: "", in_s: now() });

  const plan = (await page.evaluate(() => window.__antiDemo?.plan ?? null)) as Plan | null;
  expect(plan, "plan resolved").not.toBeNull();
  const steps = plan!.steps;
  expect(steps[0].scene_key).toBe("title");
  expect(steps.at(-1)!.scene).toBe(SCENES);
  expect([...new Set(steps.slice(1).map((s) => s.scene))]).toEqual(Array.from({ length: SCENES }, (_, i) => i + 1));
  console.log(`[diligence-demo] picks ${JSON.stringify(plan!.picks)}`);
  noErrors("title");
  await page.waitForTimeout(DWELL.title * PACE * 1000);
  marks[0].out_s = now();

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
    marks.push({ key, scene: step.scene, caption: step.caption, in_s: now() });
    const state = await page.evaluate(() => window.__antiDemo!.state());
    console.log(`[diligence-demo] ${pad2(step.scene)} ${key.padEnd(16)} settled ${((Date.now() - t) / 1000).toFixed(1)}s  ${state.path ?? "(card)"}`);

    if (step.scene_key === "final") {
      const fin = page.locator('[data-demo="demo-final"]');
      await expect(fin).toBeVisible();
      await expect(fin).toContainText("RADAR FINDS IT.");
      await expect(fin).toContainText("DILIGENCE TESTS IT.");
      await expect(fin).toContainText("PEOPLE DECIDE.");
    } else {
      await expect(page.locator('[data-demo="demo-counter"]')).toHaveText(`${pad2(step.scene)} / ${SCENES}`);
      await expect(page.locator('[data-demo="demo-scene-title"]')).toHaveText(step.title);
      await expect(page.locator('[data-demo="demo-caption"]')).toHaveText(step.caption);
      const expected = step.url ?? step.match;
      expect(state.path, `frame URL at scene ${step.scene}`).toBeTruthy();
      if (expected) expect(state.path!.split("?")[0].startsWith(expected.split(/[?#]/)[0]), `${state.path} ~ ${expected}`).toBe(true);
      if (step.ready) await expect(stage.locator(step.ready).first()).toBeAttached();
    }
    noErrors(`scene ${step.scene} (${key})`);

    // Functional pass only: exercise the presenter controls without disturbing a recording.
    if (!RECORD && step.scene_key === "ledger") {
      await page.keyboard.press("h");
      await expect(page.locator('[data-demo="demo-bar"]')).toHaveAttribute("aria-hidden", "true");
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

  const seconds = now();
  console.log(`[diligence-demo] tour finished in ${seconds.toFixed(1)}s (take ≈ ${(seconds - takeStart).toFixed(1)}s)`);
  if (RECORD) {
    const video = page.video();
    const meta = { video: video ? await video.path() : null, take_start_s: takeStart, total_s: seconds, marks };
    fs.writeFileSync(path.join(testInfo.project.outputDir, "diligence-tour-meta.json"), JSON.stringify(meta, null, 2));
  }
});
