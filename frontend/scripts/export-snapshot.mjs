#!/usr/bin/env node
/**
 * Export the verified snapshot that the public deployment serves without a backend (DATA_SOURCE=snapshot).
 *
 * Every page of the site is rendered once against a backend loaded with the verified real-data snapshot,
 * while the frontend's recording hook (lib/snapshot-server.ts, SNAPSHOT_RECORD=1) writes each backend GET
 * response into frontend/snapshot/recording/; they are then packed into snapshot/responses.pack (+ index).
 * Nothing is synthesised: the archive is exactly what the API returned.
 *
 *   # 1. a backend serving the verified snapshot (backend/radar/demo/snapshot, replayed into radar_demo)
 *   cd backend && DATABASE_URL=…/radar_demo DEMO_SNAPSHOT=true LLM_PROVIDER=none uv run uvicorn radar.api.main:app --port 8000
 *   # 2. a production build of the frontend in recording mode
 *   cd frontend && npx next build && SNAPSHOT_RECORD=1 BACKEND_URL=http://localhost:8000 \
 *       DEMO_BACKEND_URL=http://localhost:8000 npx next start -p 3100
 *   # 3. crawl (writes snapshot/responses.pack + responses.index.json, search-index.json, meta.json)
 *   node scripts/export-snapshot.mjs --site http://localhost:3100 --api http://localhost:8000
 *   # 4. optional: walk /demo once so every client-side call is recorded too
 *   cd ../e2e && BASE_URL=http://localhost:3100 npx playwright test
 */
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const ROOT = path.resolve(FRONTEND, "..");
const OUT = path.join(FRONTEND, "snapshot");

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
);
const SITE = (args.site ?? "http://localhost:3100").replace(/\/$/, "");
const API = (args.api ?? "http://localhost:8000").replace(/\/$/, "");
const CONCURRENCY = Number(args.concurrency ?? 8);

const THEMES = ["ai", "robotics", "defense", "energy", "semiconductors", "manufacturing", "frontier_infrastructure"];
const COMPANY_SORTS = ["score", "delta", "attention_gap", "recent", "stars"];
const COMPANY_STAGES = ["formation", "pre_seed", "seed", "series_a", "series_b", "unknown"];
const COMPANY_KINDS = ["company", "stealth", "project", "lab"];
const FOUNDER_SORTS = ["score", "delta", "recent", "followers"];
const COMPANY_PAGE = 50;
const FOUNDER_PAGE = 40;

async function api(p) {
  const res = await fetch(`${API}/api${p}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`API ${res.status} on ${p}`);
  return res.json();
}

async function all(listPath) {
  const out = [];
  for (let offset = 0; ; offset += 200) {
    const page = await api(`${listPath}${listPath.includes("?") ? "&" : "?"}limit=200&offset=${offset}`);
    const rows = Array.isArray(page) ? page : page.items ?? page.results ?? [];
    out.push(...rows);
    if (rows.length < 200) return out;
  }
}

const failures = [];
let done = 0;
async function visit(url) {
  try {
    const res = await fetch(`${SITE}${url}`, { headers: { Accept: url.startsWith("/api/") ? "application/json" : "text/html" } });
    await res.arrayBuffer();
    if (res.status >= 500) failures.push(`${res.status} ${url}`);
  } catch (err) {
    failures.push(`ERR ${url} ${err.message}`);
  }
  done += 1;
  if (done % 200 === 0) process.stdout.write(`  … ${done} requests\n`);
}

async function pool(urls) {
  const queue = [...new Set(urls)];
  await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
    while (queue.length) await visit(queue.shift());
  }));
}

async function main() {
  console.log(`export-snapshot: site ${SITE}, api ${API}`);
  const RECORDING = path.join(OUT, "recording");
  fs.rmSync(RECORDING, { recursive: true, force: true });
  fs.mkdirSync(RECORDING, { recursive: true });

  const [companies, people, formation, reports, archive, meet] = await Promise.all([
    all("/companies"),
    all("/founders"),
    api("/formation"),
    api("/reports?limit=200"),
    api("/brief/archive").catch(() => []),
    api("/meet?week=current"),
  ]);
  const candidates = ["high", "emerging", "early"].flatMap((t) => formation[t] ?? []);
  console.log(`  ${companies.length} companies, ${people.length} people, ${candidates.length} formation candidates, ${reports.length} reports`);

  const pages = [
    "/", "/?new=today", "/?new=week", "/formation", "/meet", "/meet?week=current", "/founders", "/companies", "/thesis",
    "/daily", "/brief", "/brief/today", "/status", "/watchlist", "/retrospective", "/search", "/unlock", "/demo",
    ...["high", "emerging", "early"].map((t) => `/formation?tier=${t}`),
    ...THEMES.map((t) => `/formation?theme=${t}`),
    ...COMPANY_SORTS.map((s) => `/companies?sort=${s}`),
    ...COMPANY_STAGES.map((s) => `/companies?stage=${s}`),
    ...COMPANY_KINDS.map((k) => `/companies?kind=${k}`),
    ...THEMES.map((t) => `/companies?theme=${t}`),
    "/companies?demo=0",
    ...FOUNDER_SORTS.map((s) => `/founders?sort=${s}`),
    ...THEMES.map((t) => `/founders?theme=${t}`),
    "/founders?pre=1", "/founders?demo=0",
    ...(Array.isArray(archive) ? archive : []).map((b) => `/brief/${b.day ?? b.date}`).filter((u) => !u.endsWith("/undefined")),
    ...candidates.map((c) => `/formation/${c.slug}`),
    ...companies.map((c) => `/companies/${c.slug}`),
    ...people.map((p) => `/founders/${p.slug}`),
    ...reports.map((r) => `/reports/${r.id}`),
  ];

  // Client-side reads (browser → /api/proxy): demo warm-up, Why drawer, formation score, load-more, downloads.
  const meetTop = (meet.people ?? [])[0];
  const proxy = [
    "/health", "/home", "/formation", "/meet?week=current", "/companies?sort=attention_gap&limit=40",
    "/reports?kind=brief&limit=50", "/brief/markdown",
    ...(meetTop ? [`/reports?entity_id=${meetTop.person.id}&kind=meeting_brief&limit=5`] : []),
    ...candidates.map((c) => `/formation/${c.id}/score`),
    ...reports.map((r) => `/reports/${r.id}/markdown`),
    ...Array.from({ length: Math.ceil(companies.length / COMPANY_PAGE) }, (_, i) => `/companies?limit=${COMPANY_PAGE}&offset=${(i + 1) * COMPANY_PAGE}`),
    ...Array.from({ length: Math.ceil(people.length / FOUNDER_PAGE) }, (_, i) => `/founders?limit=${FOUNDER_PAGE}&offset=${(i + 1) * FOUNDER_PAGE}`),
  ].map((p) => `/api/proxy${p}`);

  console.log(`  rendering ${pages.length} pages and ${proxy.length} client reads …`);
  await pool([...pages, ...proxy]);

  // Search index: every exported entity (its page was rendered above), matched by name/slug/subtitle.
  const index = [
    ...companies.map((c) => ({ entity_type: "company", id: c.id, slug: c.slug, name: c.name, subtitle: c.one_liner ?? null, score: c.score ?? null })),
    ...people.map((p) => ({ entity_type: "person", id: p.id, slug: p.slug, name: p.name, subtitle: p.headline ?? p.current_company ?? null, score: p.founder_score ?? null })),
  ].map((e) => ({ ...e, subtitle: typeof e.subtitle === "string" ? e.subtitle.slice(0, 160) : null, score: typeof e.score === "number" ? e.score : null, terms: `${e.name} ${e.slug} ${e.subtitle ?? ""}`.toLowerCase() }));
  index.sort((a, b) => a.entity_type.localeCompare(b.entity_type) || a.slug.localeCompare(b.slug));
  fs.writeFileSync(path.join(OUT, "search-index.json"), JSON.stringify(index));

  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "backend/radar/demo/snapshot/MANIFEST.json"), "utf8"));
  let commit = null;
  try {
    commit = execSync("git rev-parse --short HEAD", { cwd: ROOT }).toString().trim();
  } catch {
    /* not a git checkout */
  }
  // Pack the recorded responses (one gzip file each) into responses.pack + responses.index.json, sorted by key
  // so the same data always produces the same two files.
  const recorded = execSync(`find ${JSON.stringify(RECORDING)} -type f -name "*.json.gz"`).toString().split("\n").filter(Boolean);
  const entries = recorded.map((file) => {
    const gz = fs.readFileSync(file);
    return { key: JSON.parse(gunzipSync(gz).toString("utf8")).key, gz };
  });
  entries.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const indexOut = {};
  let offset = 0;
  const chunks = [];
  for (const e of entries) {
    indexOut[e.key] = [offset, e.gz.length];
    chunks.push(e.gz);
    offset += e.gz.length;
  }
  fs.writeFileSync(path.join(OUT, "responses.pack"), Buffer.concat(chunks));
  fs.writeFileSync(path.join(OUT, "responses.index.json"), JSON.stringify(indexOut));
  fs.rmSync(RECORDING, { recursive: true, force: true });
  fs.rmSync(path.join(OUT, "data"), { recursive: true, force: true });
  const files = entries.length;
  const meta = {
    as_of: manifest.as_of,
    captured_at: manifest.captured_at,
    exported_at: new Date().toISOString(),
    source_commit: commit,
    note: "Backend API responses for every page of the public site, recorded from the verified real-data snapshot " +
      "(backend/radar/demo/snapshot). Served by lib/snapshot-server.ts when DATA_SOURCE=snapshot.",
    counts: { companies: companies.length, people: people.length, formation_candidates: candidates.length, reports: reports.length, responses: files },
  };
  fs.writeFileSync(path.join(OUT, "meta.json"), JSON.stringify(meta, null, 2) + "\n");

  console.log(`export-snapshot: ${files} responses recorded`);
  if (failures.length) {
    console.log(`export-snapshot: ${failures.length} failures:\n  ${failures.slice(0, 40).join("\n  ")}`);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
