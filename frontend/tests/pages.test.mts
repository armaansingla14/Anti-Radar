/**
 * Pages workstream (M4–M6): entity-page display logic (WHY NOW evidence, collision line, velocity,
 * family presence, lab contributions, formation evidence, relationships, X mode), Daily v2 row
 * normalization, brief date helpers and the stored-brief fetchers (404 → honest "no brief").
 */
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import {
  archiveItemCount,
  briefDateLabel,
  briefKicker,
  dailyRows,
  DAILY_SECTIONS,
  getDailyV2,
  loadBriefArchive,
  loadBriefDay,
  parseBriefDateParam,
  shiftDay,
  toDailyItem,
} from "../lib/api-pages.ts";
import {
  attentionExplanation,
  collisionLine,
  familyKey,
  familyPresence,
  formationEvidence,
  formationsFor,
  isAutomationName,
  isXPostUrl,
  labContributions,
  labContributionText,
  narrativeLabel,
  recentChanges,
  relationshipsFromGraph,
  sectionNumbers,
  velocityHighlights,
  whyNowEvidence,
  xModeCopy,
  xModeFromStatus,
} from "../components/entity/entityLogic.ts";
import type { AntiScore, Collision, EvidenceItem, FormationCard, GraphOut, TimelineEvent, VelocityMetric } from "../lib/types.ts";

const ev = (title: string, source_type: string, event_at: string | null, signal_id: string | null = null): EvidenceItem => ({
  signal_id,
  title,
  detail: null,
  source_type,
  source_url: `https://example.com/${encodeURIComponent(title)}`,
  event_at,
  weight: null,
});

const collision = (over: Partial<Collision> = {}): Collision => ({
  score: 0.6,
  families: ["arxiv", "github"],
  window_days: 42,
  span_days: 4.6,
  signal_count: 6,
  evidence: [],
  summary: "Two independent families converged",
  ...over,
});

const score = (over: Partial<AntiScore> = {}): AntiScore => ({
  total: 50,
  delta_7d: null,
  delta_30d: null,
  components: [],
  attention_gap: null,
  collision: null,
  version: "t",
  computed_on: "2026-10-20",
  feedback_adjustment: 0,
  cohort_size: 10,
  low_coverage: false,
  ...over,
});

describe("entity: families and collision", () => {
  it("maps source types to the six families, 'other' to null", () => {
    assert.equal(familyKey("arxiv"), "research");
    assert.equal(familyKey("hackernews"), "community");
    assert.equal(familyKey("github:self-reported"), "github");
    assert.equal(familyKey("manual"), null);
    assert.equal(familyKey(null), null);
  });

  it("uses backend strength + sentence when present", () => {
    const l = collisionLine(collision({ strength: "HIGH", sentence: "4 independent sources within 17 days", family_labels: ["GitHub", "Research"] }));
    assert.deepEqual(l, { strength: "HIGH", sentence: "4 independent sources within 17 days", families: ["GitHub", "Research"] });
  });

  it("never invents a strength for older backends", () => {
    const l = collisionLine(collision());
    assert.equal(l?.strength, null);
    assert.equal(l?.sentence, "Two independent families converged");
    assert.deepEqual(l?.families, ["GitHub", "Research"]);
    assert.equal(collisionLine(null), null);
    assert.equal(collisionLine(collision({ families: [] })), null);
  });
});

describe("entity: WHY NOW evidence", () => {
  it("dedupes, drops undated items, prefers family breadth, sorts newest first", () => {
    const s = score({
      collision: collision({ evidence: [ev("A", "github", "2026-10-11T00:00:00Z", "1"), ev("B", "arxiv", "2026-10-15T00:00:00Z", "2")] }),
      components: [
        { key: "x", label: "X", value: 1, max: 2, explanation: "", raw: {}, evidence: [ev("A", "github", "2026-10-11T00:00:00Z", "1"), ev("C", "github", "2026-10-18T00:00:00Z", "3"), ev("U", "github", null)] },
      ],
    });
    const out = whyNowEvidence(s, 2);
    assert.deepEqual(out.map((e) => e.title), ["C", "B"]);
    assert.deepEqual(whyNowEvidence(s, 10).map((e) => e.title), ["C", "B", "A"]);
    assert.deepEqual(whyNowEvidence(null), []);
  });
});

describe("entity: what changed", () => {
  it("keeps only events inside the window, newest first", () => {
    const t = (day: string): TimelineEvent => ({ day, title: day, detail: null, source_type: "github", source_url: null, signal_type: "x", epistemic: "FACT", is_demo: false });
    const out = recentChanges([t("2026-09-01"), t("2026-10-19"), t("2026-10-01")], new Date("2026-10-20T12:00:00Z"), 30);
    assert.deepEqual(out.map((e) => e.day), ["2026-10-19", "2026-10-01"]);
  });

  it("orders velocity by information, drops 7d duplicates of acceleration, keeps insufficient history", () => {
    const v = (key: string, basis: VelocityMetric["basis"] = "backfill"): VelocityMetric => ({ key, label: key, value: basis === "insufficient_history" ? null : 1, unit: null, basis, human: key });
    const out = velocityHighlights([v("star_velocity_7d"), v("issue_velocity_30d", "insufficient_history"), v("star_acceleration"), v("star_velocity_prev_7d")]);
    assert.deepEqual(out.map((x) => x.key), ["star_acceleration", "issue_velocity_30d"]);
    assert.deepEqual(velocityHighlights(undefined), []);
  });
});

describe("entity: family presence", () => {
  it("reports observed / silent / unavailable honestly", () => {
    const fams = familyPresence(
      { updated_at: null, last_observed_at: null, age_days: null, families: { github: "2026-10-20T06:00:00Z", research: null } },
      [ev("p", "arxiv", "2026-10-15T00:00:00Z")],
      { socialAvailable: false, hiringAvailable: false },
    );
    const by = Object.fromEntries(fams.map((f) => [f.key, f]));
    assert.equal(fams.length, 6);
    assert.equal(by.github.state, "observed");
    assert.equal(by.research.state, "observed");
    assert.equal(by.research.inWindow, 1);
    assert.equal(by.web.state, "silent");
    assert.equal(by.hiring.state, "unavailable");
    assert.equal(by.social.state, "unavailable");
    assert.match(by.social.note ?? "", /API access not configured/);
  });
});

describe("entity: attention gap", () => {
  it("never claims a gap when attention is unmeasured", () => {
    const text = attentionExplanation({ technical: 92, attention: null, gap: null, explanation: null });
    assert.match(text, /not measured/);
    assert.match(text, /no gap is claimed/);
    assert.equal(attentionExplanation({ technical: 1, attention: 2, gap: 3, explanation: "backend says" }), "backend says");
  });
});

describe("entity: person signals", () => {
  const s = score({
    components: [
      {
        key: "pedigree",
        label: "Pedigree",
        value: 15,
        max: 15,
        explanation: "",
        evidence: [],
        raw: {
          lab_contributions: [
            { org: "google-deepmind", repo: "mujoco", full_name: "google-deepmind/mujoco", label: "Google DeepMind", tier: 1, commits: 123 },
            { org: "google-deepmind", repo: "mujoco_menagerie", full_name: "google-deepmind/mujoco_menagerie", label: "Google DeepMind", tier: 1, commits: 149, first_commit_at: "2022-09-04T00:00:00+00:00" },
            { full_name: "bad name with spaces", commits: 3 },
          ],
        },
      },
      {
        key: "formation",
        label: "Formation",
        value: 6.4,
        max: 20,
        explanation: "",
        evidence: [ev("new repo", "github", "2026-09-10T00:00:00Z")],
        raw: { new_projects: ["kevinzakka/mjbatch"], young_orgs: [], joined_young_org: false },
      },
    ],
  });

  it("reads verified lab contributions, most commits first", () => {
    const labs = labContributions(s);
    assert.deepEqual(labs.map((l) => l.fullName), ["google-deepmind/mujoco_menagerie", "google-deepmind/mujoco"]);
    assert.equal(labContributionText(labs[0]), "149 commits to google-deepmind/mujoco_menagerie");
    assert.equal(labContributionText({ commits: 1, fullName: "a/b" }), "1 commit to a/b");
    assert.equal(labContributionText({ commits: null, fullName: "a/b" }), "Contributed to a/b");
    assert.equal(labs[0].url, "https://github.com/google-deepmind/mujoco_menagerie");
    assert.deepEqual(labContributions(null), []);
  });

  it("unpacks formation evidence", () => {
    const f = formationEvidence(s);
    assert.deepEqual(f.newProjects, ["kevinzakka/mjbatch"]);
    assert.equal(f.joinedYoungOrg, false);
    assert.equal(f.evidence.length, 1);
    assert.equal(formationEvidence(null).value, null);
  });

  it("finds formation candidates by anchor or member", () => {
    const card = (id: string, score: number, anchor: string, members: string[]) =>
      ({ id, slug: id, score, anchor: { type: "company", id: anchor, slug: null, name: "" }, members: members.map((m) => ({ id: m })) }) as unknown as FormationCard;
    const feed = { high: [card("a", 80, "c1", ["p1"])], emerging: [], early: [card("b", 90, "c2", ["p2", "p1"]), card("c", 10, "c3", ["p3"])] };
    assert.deepEqual(formationsFor(feed, "p1").map((c) => c.id), ["b", "a"]);
    assert.deepEqual(formationsFor(feed, "c3").map((c) => c.id), ["c"]);
    assert.deepEqual(formationsFor(null, "p1"), []);
  });

  it("derives relationships from graph edges: WORKED_WITH inferred, automation dropped", () => {
    const g: GraphOut = {
      nodes: [
        { id: "me", type: "person", label: "Me", slug: "me", score: null, is_demo: false },
        { id: "a", type: "person", label: "Ada", slug: "ada", score: null, is_demo: false },
        { id: "b", type: "person", label: "Copybara Service", slug: "cb", score: null, is_demo: false },
        { id: "r", type: "repository", label: "o/r", slug: null, score: null, is_demo: false },
        { id: "z", type: "person", label: "Zed", slug: "z", score: null, is_demo: false },
      ],
      edges: [
        { source: "me", target: "a", kind: "contributed_with" },
        { source: "a", target: "me", kind: "worked_with" },
        { source: "b", target: "me", kind: "contributed_with" },
        { source: "me", target: "r", kind: "contributor" },
        { source: "a", target: "z", kind: "coauthored_with" },
        { source: "me", target: "a", kind: "contributed_with" },
      ],
    };
    const rels = relationshipsFromGraph(g, "me");
    assert.deepEqual(
      rels.map((r) => [r.kind, r.epistemic]),
      [
        ["CONTRIBUTED_WITH", "VERIFIED"],
        ["WORKED_WITH", "INFERRED"],
        ["CONTRIBUTED_TO", "VERIFIED"],
      ],
    );
    assert.ok(isAutomationName("dependabot[bot]"));
    assert.ok(!isAutomationName("Abbot Jones"));
  });

  it("labels model text as ANALYSIS, never fact", () => {
    assert.equal(narrativeLabel({ generator: "llm", epistemic: "FACT" }), "ANALYSIS");
    assert.equal(narrativeLabel({ generator: "template", epistemic: "INFERENCE" }), "INFERRED");
    assert.equal(narrativeLabel(null), "INFERRED");
  });
});

describe("entity: X mode", () => {
  it("reads provider state honestly", () => {
    assert.equal(xModeFromStatus({ state: "disabled", reason: "API access not configured (X_BEARER_TOKEN)" }), "disabled");
    assert.equal(xModeFromStatus({ state: "never_run", reason: "API access not configured" }), "disabled");
    assert.equal(xModeFromStatus({ state: "healthy" }), "official");
    assert.equal(xModeFromStatus(undefined), "unknown");
    assert.equal(xModeCopy("disabled").line, "X intelligence unavailable — API access not configured");
  });

  it("accepts only x.com / twitter.com status URLs", () => {
    assert.ok(isXPostUrl("https://x.com/karpathy/status/123"));
    assert.ok(isXPostUrl(" https://twitter.com/a_b/status/9 "));
    assert.ok(!isXPostUrl("https://x.com/karpathy"));
    assert.ok(!isXPostUrl("https://evil.com/x.com/a/status/1"));
  });
});

describe("entity: section numbering", () => {
  it("numbers rendered sections in reading order", () => {
    const m = sectionNumbers(["why-now", false, "facts", null, "sources"]);
    assert.deepEqual([...m.entries()], [["why-now", 1], ["facts", 2], ["sources", 3]]);
  });
});

describe("daily v2 rows", () => {
  it("normalizes the backend DailyItem (sources = evidence list)", () => {
    const row = toDailyItem(
      {
        kind: "formation",
        headline: "2 people active around AetherLabs-AI",
        why_now: "Newly detected · 2 independent source families",
        at: "2026-10-20T06:00:00Z",
        sources: [ev("site", "website", "2026-10-20T00:00:00Z"), ev("repo", "github", "2026-10-19T00:00:00Z")],
        confidence: "MEDIUM CONFIDENCE",
        entity: { type: "company", id: "1", slug: "aetherlabs-ai", name: "AetherLabs-AI" },
        label: "FORMATION SIGNALS DETECTED",
      },
      0,
    );
    assert.ok(row);
    assert.equal(row.sources, 2);
    assert.equal(row.evidence.length, 2);
    assert.equal(row.href, "/formation/aetherlabs-ai");
    assert.equal(row.confidence, "MEDIUM CONFIDENCE");
    assert.equal(row.label, "FORMATION SIGNALS DETECTED");
  });

  it("links people and companies, drops rows without a headline, never repeats the headline as why-now", () => {
    const rows = dailyRows([
      { kind: "person", headline: "Ada", why_now: "Ada", entity: { type: "person", id: "p", slug: "ada", name: "Ada" }, sources: [] },
      { kind: "company", headline: "", why_now: "x" },
      null,
    ]);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].href, "/founders/ada");
    assert.equal(rows[0].whyNow, null);
    assert.equal(rows[0].sources, 0);
  });

  it("covers the seven spec sections in order", () => {
    assert.deepEqual(DAILY_SECTIONS.map((s) => s.title), [
      "What changed",
      "People to know",
      "Formations",
      "Companies accelerating",
      "Research that matters",
      "Strange signals",
      "Watchlist changes",
    ]);
  });
});

describe("brief helpers", () => {
  it("validates date params", () => {
    assert.equal(parseBriefDateParam("today"), "today");
    assert.equal(parseBriefDateParam("2026-10-20"), "2026-10-20");
    assert.equal(parseBriefDateParam("2026-02-30"), null);
    assert.equal(parseBriefDateParam("20261020"), null);
    assert.equal(parseBriefDateParam("../etc"), null);
  });

  it("formats the kicker from the real item count", () => {
    assert.equal(briefDateLabel("2026-10-20"), "OCTOBER 20");
    assert.equal(briefKicker("2026-10-20", 5, true), "ANTI RADAR · OCTOBER 20 · 5 things worth knowing today");
    assert.equal(briefKicker("2026-10-19", 1, false), "ANTI RADAR · OCTOBER 19 · 1 thing worth knowing that day");
  });

  it("shifts days across month boundaries and counts archive items", () => {
    assert.equal(shiftDay("2026-11-01", -1), "2026-10-31");
    assert.equal(shiftDay("bad", 1), null);
    assert.equal(archiveItemCount({ items: 3 }), 3);
    assert.equal(archiveItemCount({ items: [1, 2] }), 2);
    assert.equal(archiveItemCount({ items: null }), null);
  });
});

describe("pages API client", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });
  const mock = (status: number, body: unknown, seen: string[] = []) => {
    process.env.BACKEND_URL = "http://api:8000";
    globalThis.fetch = (async (url: string | URL) => {
      seen.push(String(url));
      return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    return seen;
  };

  it("hits /api/daily/v2 and /api/brief/day/{d}", async () => {
    const seen = mock(200, { date: "2026-10-20" });
    await getDailyV2({ since: "2026-10-19T00:00:00Z" });
    await loadBriefDay("2026-10-19");
    assert.match(seen[0], /\/api\/daily\/v2\?since=2026-10-19T00%3A00%3A00Z$/);
    assert.match(seen[1], /\/api\/brief\/day\/2026-10-19$/);
  });

  it("returns null (honest 'no brief') on 404 no_brief_for_date, 'unavailable' otherwise", async () => {
    mock(404, { detail: "no_brief_for_date" });
    assert.equal(await loadBriefDay("2026-10-01"), null);
    mock(404, { detail: "Not Found" });
    assert.equal(await loadBriefDay("2026-10-01"), "unavailable");
    mock(500, { detail: "boom" });
    assert.equal(await loadBriefDay("today"), "unavailable");
  });

  it("archive: list on success, null when unavailable", async () => {
    mock(200, [{ date: "2026-10-20", id: "x", title: "t", items: 1 }]);
    assert.equal((await loadBriefArchive())?.length, 1);
    mock(503, { detail: "down" });
    assert.equal(await loadBriefArchive(), null);
  });
});
