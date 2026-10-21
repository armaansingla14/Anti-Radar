// Usage: node shot.mjs <url-or-path> <out.png> [--full] [--width=1440] [--height=900] [--wait=ms]
// Headless screenshot for visual QA. Chromium libs not present on the host are loaded
// from ~/.local/chromelibs when that directory exists (a machine-specific shim; harmless elsewhere).
import { chromium } from "playwright";
import os from "node:os";
import path from "node:path";

const [, , target = "/", out = "shot.png", ...flags] = process.argv;
const opt = Object.fromEntries(flags.map((f) => f.replace(/^--/, "").split("=")).map(([k, v]) => [k, v ?? true]));
const libdir = path.join(os.homedir(), ".local/chromelibs/usr/lib/x86_64-linux-gnu");
process.env.LD_LIBRARY_PATH = [libdir, process.env.LD_LIBRARY_PATH].filter(Boolean).join(":");
const base = process.env.BASE_URL || "http://localhost:3000";
const url = target.startsWith("http") ? target : base + target;

const browser = await chromium.launch({ env: { ...process.env } });
const page = await browser.newPage({
  viewport: { width: Number(opt.width || 1440), height: Number(opt.height || 900) },
  deviceScaleFactor: Number(opt.scale || 1),
});
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
if (opt.wait) await page.waitForTimeout(Number(opt.wait));
await page.screenshot({ path: out, fullPage: Boolean(opt.full) });
console.log(JSON.stringify({ url, status: resp?.status(), out, errors: errors.slice(0, 10) }));
await browser.close();
