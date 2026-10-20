// Demo stills from a running stack: demo/screenshots/01-radar.png … 07-anti-brief.png
// (1920×1080 viewport, deviceScaleFactor 2, clipped to the meaningful region of each page).
//
// Usage: BASE_URL=http://localhost:3000 node demo-screenshots.mjs [outDir]
//
// Entities are not hard-coded: the script opens /demo (which also selects the demo dataset and wakes the
// backend), reads the plan the presenter resolved from the data, then visits those pages directly.
import { chromium } from "playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] || path.join(here, "../demo/screenshots"));
const base = process.env.BASE_URL || "http://localhost:3000";
process.env.LD_LIBRARY_PATH = [path.join(os.homedir(), ".local/chromelibs/usr/lib/x86_64-linux-gnu"), process.env.LD_LIBRARY_PATH]
  .filter(Boolean)
  .join(":");
fs.mkdirSync(out, { recursive: true });

const W = 1920;
const browser = await chromium.launch({ env: { ...process.env } });
const page = await browser.newPage({ viewport: { width: W, height: 1080 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

// 1. Resolve the plan through /demo.
await page.goto(base + "/demo", { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-demo="demo-start"][data-demo-state="ready"]', { timeout: 240_000 });
const plan = await page.evaluate(() => window.__antiDemo.plan);
const urlOf = (key, beat = 0) => plan.steps.filter((s) => s.scene_key === key)[beat]?.url ?? null;

async function go(url) {
  await page.goto(base + url, { waitUntil: "networkidle", timeout: 90_000 });
  await page.evaluate(() => document.fonts.ready);
  // Presentation only: no scrollbar in the stills.
  await page.addStyleTag({ content: "html{scrollbar-width:none}html::-webkit-scrollbar{display:none}" });
  await page.waitForTimeout(500);
}

/** Page-coordinate box of a selector (top/bottom), or null. */
async function box(selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top + window.scrollY, bottom: r.bottom + window.scrollY, left: r.left, right: r.right };
  }, selector);
}

/** Content column (the 1240px masthead width) with a little paper on each side. */
const COL = { x: (W - 1240) / 2 - 24, width: 1240 + 48 };

async function shot(name, { top = 0, bottom, x = COL.x, width = COL.width }) {
  const height = Math.max(200, Math.round(bottom - top));
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true, clip: { x, y: Math.max(0, Math.round(top)), width, height } });
  console.log(`captured ${name}.png  ${width}×${height} @2x`);
}

// 01 — the radar (home): masthead, today in numbers, what changed, people to meet.
await go("/");
await shot("01-radar", { top: 0, bottom: 1080 });

// 02 — Formation Radar: heading through the top candidate's card.
await go("/formation");
{
  const slug = plan.picks.candidate?.slug;
  const card = slug ? await box(`[data-demo="formation-card"][data-demo-slug="${slug}"]`) : null;
  await shot("02-formation", { top: 0, bottom: card ? card.bottom + 6 : 1300 });
}

// 03 — Why now: the candidate's header, dated WHY NOW evidence and the score anatomy.
const candidateUrl = urlOf("collision");
if (candidateUrl) {
  await go(candidateUrl);
  const score = await box("#score");
  const why = await box("#why-now");
  await shot("03-why-now", { top: 130, bottom: Math.max(score?.bottom ?? 0, why?.bottom ?? 0) + 36 });
}

// 04 — the person: header, why now and the verified frontier-lab trajectory.
const personUrl = urlOf("person");
if (personUrl) {
  await go(personUrl);
  const traj = await box("#trajectory");
  await shot("04-founder", { top: 0, bottom: (traj?.bottom ?? 1300) + 32 });
}

// 05 — who should we meet: heading through recommendation #1.
await go("/meet");
{
  const row = await box('[data-demo="meet-row"][data-demo-rank="1"]');
  await shot("05-people-to-meet", { top: 0, bottom: row ? row.bottom + 8 : 1500 });
}

// 06 — attention gap: the company header strip, then the Anti Score / Attention Gap pair.
const companyUrl = urlOf("attention");
if (companyUrl) {
  await go(companyUrl);
  const gap = await box("#attention-gap");
  const score = await box("#anti-score");
  const top = Math.min(gap?.top ?? 0, score?.top ?? gap?.top ?? 0) - 40;
  const bottom = Math.max(gap?.bottom ?? 0, score?.bottom ?? 0) + 40;
  await shot("06-attention-gap", { top, bottom });
}

// 07 — the Anti Brief (existing report, else the one the tour generated on its first run).
let briefUrl = urlOf("brief");
if (!briefUrl || !briefUrl.startsWith("/reports/")) {
  const reports = await (await fetch(`${base}/api/proxy/reports?kind=brief&limit=5`, { headers: { cookie: "radar_dataset=demo" } })).json().catch(() => []);
  briefUrl = Array.isArray(reports) && reports[0] ? `/reports/${reports[0].id}` : null;
}
if (briefUrl?.startsWith("/reports/")) {
  await go(briefUrl);
  const art = await box('[data-demo="report"]');
  await shot("07-anti-brief", { top: 130, bottom: Math.min((art?.bottom ?? 1400), 1450) });
} else {
  console.log("skipped 07-anti-brief.png — no Anti Brief exists yet (run the tour once to generate one)");
}

console.log(JSON.stringify({ out, errors: errors.slice(0, 10) }));
await browser.close();
