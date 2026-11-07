/**
 * Playwright config for the presenter tours: /demo (demo-tour.spec.ts), /demo/diligence (diligence-tour.spec.ts) and
 * /demo/portfolio (portfolio-tour.spec.ts).
 *
 *   npx playwright test -c playwright.config.ts demo-tour.spec.ts             # fast functional pass of one tour
 *   RECORD=1 npx playwright test -c playwright.config.ts demo-tour.spec.ts    # paced run, 1920×1080 video (see record-demo.sh)
 *
 * BASE_URL defaults to http://localhost:3000. Chromium host libraries missing on this machine are loaded
 * from ~/.local/chromelibs (same as shot.mjs / demo-screenshots.mjs).
 */
import { defineConfig } from "@playwright/test";
import os from "node:os";
import path from "node:path";

const libdir = path.join(os.homedir(), ".local/chromelibs/usr/lib/x86_64-linux-gnu");
process.env.LD_LIBRARY_PATH = [libdir, process.env.LD_LIBRARY_PATH].filter(Boolean).join(":");

const RECORD = process.env.RECORD === "1";
const env = Object.fromEntries(Object.entries(process.env).filter((e): e is [string, string] => typeof e[1] === "string"));

export default defineConfig({
  testDir: ".",
  testMatch: /(demo|diligence|portfolio)-tour\.spec\.ts$/,
  outputDir: "test-results",
  timeout: 12 * 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    video: RECORD ? { mode: "on", size: { width: 1920, height: 1080 } } : "off",
    trace: "off",
    launchOptions: { env },
  },
});
