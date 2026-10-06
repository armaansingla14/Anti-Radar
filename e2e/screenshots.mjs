// Captures the README screenshots from a running stack (BASE_URL, default http://localhost:3000).
// Usage: node screenshots.mjs [outDir]
import { chromium } from "playwright";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] || "../docs/screenshots";
const base = process.env.BASE_URL || "http://localhost:3000";
process.env.LD_LIBRARY_PATH = [path.join(os.homedir(), ".local/chromelibs/usr/lib/x86_64-linux-gnu"), process.env.LD_LIBRARY_PATH]
  .filter(Boolean).join(":");

const browser = await chromium.launch({ env: { ...process.env } });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const go = (p) => page.goto(base + p, { waitUntil: "networkidle", timeout: 60000 });
const shot = async (name, opts = {}) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/${name}.png`, ...opts });
  console.log("captured", name);
};

await go("/");
await shot("01-radar", { clip: { x: 0, y: 0, width: 1440, height: 1500 } });

await page.getByRole("button", { name: /^Why \d+\?/ }).first().click();
await page.waitForTimeout(800);
await page.evaluate(() => window.scrollTo(0, 0));
await shot("02-why-score");
await page.keyboard.press("Escape");

await go("/companies/halcyon-dynamics");
await shot("03-company", { clip: { x: 0, y: 0, width: 1440, height: 1700 } });

await go("/founders/mira-okafor");
await shot("04-founder", { clip: { x: 0, y: 0, width: 1440, height: 1300 } });

const reports = await (await fetch(base + "/api/proxy/reports")).json().catch(() => []);
const dexmal = Array.isArray(reports) ? reports.find((r) => r.kind === "brief" && r.entity_slug === "dexmal") : null;
if (dexmal) {
  await go(`/reports/${dexmal.id}`);
  await shot("05-anti-brief", { clip: { x: 0, y: 0, width: 1440, height: 1600 } });
}

await go("/thesis");
await shot("06-thesis", { clip: { x: 0, y: 0, width: 1440, height: 1150 } });

await go("/search?q=" + encodeURIComponent("Robotics founders who left major AI labs in the last six months"));
await shot("07-search", { clip: { x: 0, y: 0, width: 1440, height: 1100 } });

await go("/status");
await shot("08-status", { clip: { x: 0, y: 0, width: 1440, height: 1250 } });

await browser.close();
