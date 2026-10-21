#!/usr/bin/env node
/**
 * Pre-send smoke test of the public Anti Radar demo: what a reviewer clicking the links would hit.
 *
 *   node scripts/verify_public_demo.mjs                       # production (https://anti-radar-one.vercel.app)
 *   node scripts/verify_public_demo.mjs --base http://localhost:3100 --no-release
 *   node scripts/verify_public_demo.mjs --insecure            # behind a TLS-intercepting corporate proxy
 *
 * 1. HTTP: every critical route, the video file, the release asset, and every link in README.md.
 * 2. Browser (Playwright from e2e/node_modules, clean context: no cookies, no storage): the reviewer journey
 *    README → video → app → Formation → candidate → Why now → sources → Meet → person → Attention gap →
 *    Anti Brief → home, with visible-text assertions, console errors, time to first content, and a phone-width
 *    overflow check. External source links found on the journey are checked too.
 * Exit code 1 if anything fails.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(`--${name}`);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : fallback;
};
const BASE = opt("base", "https://anti-radar-one.vercel.app").replace(/\/$/, "");
const REPO = "https://github.com/armaansingla14/Anti-Radar";
const RELEASE = `${REPO}/releases/tag/v1.0.0`;
const RELEASE_MP4 = `${REPO}/releases/download/v1.0.0/anti-radar-demo.mp4`;
const INSECURE = flag("insecure");
if (INSECURE) process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";

const results = [];
const ok = (what, detail = "") => results.push({ ok: true, what, detail });
const fail = (what, detail = "") => results.push({ ok: false, what, detail });
const warn = (what, detail = "") => results.push({ ok: null, what, detail });

async function http(url, { method = "GET", accept = "text/html" } = {}) {
  const t = Date.now();
  try {
    const res = await fetch(url, { method, redirect: "follow", headers: { Accept: accept, "User-Agent": "anti-radar-verify/1.0" } });
    const body = method === "HEAD" ? "" : await res.text();
    return { status: res.status, ms: Date.now() - t, body, type: res.headers.get("content-type") ?? "", length: Number(res.headers.get("content-length") ?? 0) };
  } catch (err) {
    return { status: 0, ms: Date.now() - t, body: "", error: err.cause?.code ?? err.message };
  }
}

const BAD_TEXT = [/signal lost/i, /backend did not answer/i, /backend is not answering/i, /waking the radar/i, /backend_waking/i, /failed to fetch/i, /not_in_snapshot/i, /localhost/i, /anti fund · internal/i, /not for distribution/i];

// ── 1. HTTP ──────────────────────────────────────────────────────────────────
const ROUTES = [
  ["/", "VERIFIED SNAPSHOT"], ["/demo", "Conviction Before Consensus"], ["/video", "Anti Radar in three minutes"],
  ["/formation", "Technical teams before the company announcement"], ["/formation/person-sameh-khamis", "Sameh Khamis"],
  ["/meet", "Who should we meet"], ["/founders", "Kevin Zakka"], ["/founders/kevin-zakka", "Kevin Zakka"],
  ["/companies", "ShengShu"], ["/companies/shengshu", "ShengShu"],
  ["/reports/a89e4ce9-2cc8-4a75-a2e1-752565e09141", "Anti Brief"], ["/reports/f6a438de-8eab-48d5-a661-c32523a00597", "Meeting Brief"],
  ["/thesis", null], ["/daily", null], ["/brief/today", null], ["/status", null], ["/search?q=vllm", "vLLM"], ["/watchlist", null],
  ["/retrospective", null],
];

async function checkHttp() {
  for (const [route, expect] of ROUTES) {
    const r = await http(`${BASE}${route}`);
    const bad = BAD_TEXT.find((re) => re.test(r.body.replace(/data-[a-z-]+="[^"]*"/g, "")));
    if (r.status !== 200) fail(`GET ${route}`, `HTTP ${r.status || r.error}`);
    else if (expect && !r.body.includes(expect)) fail(`GET ${route}`, `missing "${expect}"`);
    else if (bad) fail(`GET ${route}`, `contains ${bad}`);
    else ok(`GET ${route}`, `${r.ms} ms`);
  }
  const api = await http(`${BASE}/api/proxy/home`, { accept: "application/json" });
  api.status === 200 && api.body.includes('"is_snapshot":true') ? ok("snapshot data served (/api/proxy/home)", `${api.ms} ms`) : fail("snapshot data served (/api/proxy/home)", `HTTP ${api.status}`);
  const video = await http(`${BASE}/demo/anti-radar-demo.mp4`, { method: "HEAD", accept: "video/mp4" });
  video.status === 200 && video.type.includes("video/mp4") ? ok("video file on the site", `${(video.length / 1e6).toFixed(1)} MB`) : fail("video file on the site", `HTTP ${video.status} ${video.type}`);
  const og = await http(`${BASE}/opengraph-image.png`, { method: "HEAD", accept: "image/png" });
  og.status === 200 ? ok("social preview image") : fail("social preview image", `HTTP ${og.status}`);
  if (!flag("no-release")) {
    const rel = await http(RELEASE);
    rel.status === 200 ? ok("GitHub release page v1.0.0") : fail("GitHub release page v1.0.0", `HTTP ${rel.status || rel.error}`);
    const asset = await http(RELEASE_MP4, { method: "HEAD", accept: "*/*" });
    asset.status === 200 ? ok("release asset anti-radar-demo.mp4", `${(asset.length / 1e6).toFixed(1)} MB`) : fail("release asset anti-radar-demo.mp4", `HTTP ${asset.status || asset.error}`);
  }
}

async function checkReadme() {
  const md = fs.readFileSync(path.join(ROOT, "README.md"), "utf8");
  const links = [...md.matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1]).filter((l) => !l.startsWith("#"));
  for (const link of [...new Set(links)]) {
    if (/^https?:/.test(link)) {
      if (link.includes("/releases/") && flag("no-release")) continue;
      const r = await http(link, { method: link.endsWith(".mp4") ? "HEAD" : "GET" });
      r.status === 200 ? ok(`README → ${link}`) : fail(`README → ${link}`, `HTTP ${r.status || r.error}`);
    } else {
      fs.existsSync(path.join(ROOT, link.split("#")[0])) ? ok(`README → ${link}`) : fail(`README → ${link}`, "file missing");
    }
  }
  // Setup instructions may mention localhost; links may not. No old repository name, no relative releases link.
  if (links.some((l) => /localhost/i.test(l))) fail("README links", "a link points at localhost");
  if (/Anti-Rader|\.\.\/\.\.\/releases/i.test(md)) fail("README text", "contains Anti-Rader or a relative releases link");
}

// ── 2. Browser ───────────────────────────────────────────────────────────────
async function loadPlaywright() {
  for (const p of [path.join(ROOT, "e2e/node_modules/playwright/index.mjs"), "playwright"]) {
    try {
      return (await import(p)).chromium;
    } catch {
      /* try next */
    }
  }
  return null;
}

async function checkBrowser() {
  const chromium = await loadPlaywright();
  if (!chromium) return warn("browser checks", "Playwright not installed (cd e2e && npm ci)");
  const libs = path.join(process.env.HOME ?? "", ".local/chromelibs/usr/lib/x86_64-linux-gnu");
  if (fs.existsSync(libs)) process.env.LD_LIBRARY_PATH = `${libs}${process.env.LD_LIBRARY_PATH ? `:${process.env.LD_LIBRARY_PATH}` : ""}`;
  const browser = await chromium.launch();
  const context = await browser.newContext({ ignoreHTTPSErrors: INSECURE, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`));
  const external = new Set();

  const step = async (name, fn) => {
    const before = errors.length;
    try {
      const detail = await fn();
      errors.length > before ? fail(name, errors.slice(before).join(" | ").slice(0, 300)) : ok(name, detail ?? "");
    } catch (err) {
      fail(name, err.message.split("\n")[0].slice(0, 300));
    }
  };
  const visible = async (text) => {
    await page.getByText(text, { exact: false }).first().waitFor({ state: "visible", timeout: 15_000 });
  };
  const collectSources = async () => {
    for (const href of await page.$$eval("a[href^='http']", (as) => as.map((a) => a.href))) {
      if (!href.startsWith(BASE)) external.add(href);
    }
  };

  await step("1-2. video plays from /video", async () => {
    await page.goto(`${BASE}/video`, { waitUntil: "domcontentloaded" });
    const v = page.locator("video");
    await v.waitFor();
    const meta = await v.evaluate((el) => new Promise((resolve) => {
      if (el.readyState >= 1) return resolve({ duration: el.duration, src: el.currentSrc });
      el.addEventListener("loadedmetadata", () => resolve({ duration: el.duration, src: el.currentSrc }), { once: true });
      el.addEventListener("error", () => resolve({ duration: 0, src: el.currentSrc }), { once: true });
      el.load();
    }));
    if (!(meta.duration > 120)) throw new Error(`video metadata not loaded (duration ${meta.duration})`);
    return `${Math.round(meta.duration)} s from ${meta.src.replace(BASE, "")}`;
  });
  await step("4-5. radar home loads and is understandable", async () => {
    const t = Date.now();
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await visible("Possible formations");
    const ms = Date.now() - t;
    for (const t2 of ["VERIFIED SNAPSHOT", "People to meet", "Companies accelerating", "Signal collisions", "What changed", "Built for Anti Fund"]) await visible(t2);
    if (ms > 4000) throw new Error(`first content after ${ms} ms`);
    return `first content ${ms} ms`;
  });
  await step("6. Formation Radar", async () => {
    await page.getByRole("link", { name: "Formation", exact: true }).first().click();
    await page.waitForURL(/\/formation/);
    await visible("Technical teams before the company announcement");
    await visible("formation not yet verified");
  });
  await step("7-9. candidate, why now, sources", async () => {
    await page.goto(`${BASE}/formation/person-sameh-khamis`, { waitUntil: "domcontentloaded" });
    await visible("Sameh Khamis");
    await visible("Why now");
    await visible("self-reported");
    await collectSources();
  });
  await step("10-11. Who should we meet → person", async () => {
    await page.goto(`${BASE}/meet`, { waitUntil: "domcontentloaded" });
    await visible("Who should we meet");
    await visible("Kevin Zakka");
    await page.getByRole("link", { name: /Open profile/i }).first().click();
    await page.waitForURL(/\/founders\//);
    await visible("Kevin Zakka");
    await collectSources();
  });
  await step("meeting brief opens", async () => {
    await page.goto(`${BASE}/meet`, { waitUntil: "domcontentloaded" });
    await page.locator('[data-demo="meeting-brief"]').first().click();
    await page.waitForURL(/\/reports\//);
    await visible("Meeting Brief");
    await visible("Anti Radar · Research brief");
  });
  await step("12. Attention gap", async () => {
    await page.goto(`${BASE}/companies/shengshu`, { waitUntil: "domcontentloaded" });
    await visible("+71");
    await visible("How it's measured");
    await visible("unavailable");
    await collectSources();
  });
  await step("13. Anti Brief", async () => {
    await page.getByRole("link", { name: /Open Anti Brief/i }).first().click();
    await page.waitForURL(/\/reports\//);
    await visible("Anti Brief");
    await visible("Unknown");
    await collectSources();
  });
  await step("14. back home", async () => {
    await page.getByRole("link", { name: /Anti Radar — home/i }).first().click();
    await page.waitForURL(`${BASE}/`);
    await visible("Possible formations");
  });
  await step("/demo guided tour starts", async () => {
    await page.goto(`${BASE}/demo`, { waitUntil: "domcontentloaded" });
    await page.locator('[data-demo="demo-start"][data-demo-state="ready"]').waitFor({ timeout: 30_000 });
    await page.locator('[data-demo="demo-start"]').click();
    await page.locator('[data-demo="demo-counter"]').filter({ hasText: "02 / 10" }).waitFor({ timeout: 30_000 });
  });

  // Phone width: no horizontal overflow on the main pages.
  const phone = await browser.newContext({ ignoreHTTPSErrors: INSECURE, viewport: { width: 390, height: 844 }, isMobile: true });
  const p2 = await phone.newPage();
  for (const route of ["/", "/formation", "/meet", "/companies/shengshu", "/founders/kevin-zakka", "/video", "/demo"]) {
    try {
      await p2.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
      await p2.waitForTimeout(400);
      const over = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      over > 2 ? fail(`phone 390px ${route}`, `${over}px horizontal overflow`) : ok(`phone 390px ${route}`);
    } catch (err) {
      fail(`phone 390px ${route}`, err.message.split("\n")[0]);
    }
  }
  await browser.close();

  // Source links seen on the journey.
  const sample = [...external].filter((u) => !u.includes("/releases/")).slice(0, Number(opt("max-sources", 40)));
  let good = 0;
  const broken = [];
  for (const url of sample) {
    let r = await http(url, { method: "HEAD", accept: "*/*" });
    if (r.status === 405 || r.status === 403 || r.status === 0) r = await http(url, { accept: "*/*" });
    if (r.status >= 200 && r.status < 400) good += 1;
    else if (r.status === 429) good += 1; // rate-limited, not broken
    else broken.push(`${r.status || r.error} ${url}`);
  }
  broken.length ? fail(`source links (${good}/${sample.length} ok)`, broken.slice(0, 5).join(" | ")) : ok(`source links (${good}/${sample.length} ok)`);
}

// ── run ──────────────────────────────────────────────────────────────────────
console.log(`verify_public_demo: ${BASE}${INSECURE ? " (TLS verification off)" : ""}\n`);
await checkHttp();
await checkReadme();
if (!flag("no-browser")) await checkBrowser();
for (const r of results) console.log(`${r.ok === true ? "  ok  " : r.ok === null ? "  --  " : "  FAIL"}  ${r.what}${r.detail ? `  (${r.detail})` : ""}`);
const failed = results.filter((r) => r.ok === false).length;
console.log(`\n${results.filter((r) => r.ok).length} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
