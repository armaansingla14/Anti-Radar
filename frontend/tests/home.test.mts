/**
 * Home + trust vocabulary: epistemic display labels, confidence, source
 * families/drawer grouping, data age, attention/collision fallbacks, feedback
 * labels and legacy retry, home header/window/fallback composition, getHome.
 */
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { getHome, postFeedback } from "../lib/api.ts";
import {
  ageShort,
  attentionView,
  collisionView,
  confidenceLevel,
  countSources,
  dataAgeParts,
  epistemicDisplay,
  familyLabels,
  familyOf,
  FEEDBACK_ITEMS,
  feedbackPolarity,
  feedbackText,
  groupByFamily,
  legacyFeedbackLabel,
  sourcesLabel,
  updatedAgo,
} from "../lib/labels.ts";
import {
  changeHref,
  composeHomeFallback,
  homeStatusLine,
  inWindow,
  interleave,
  parseWindow,
  windowHref,
} from "../components/feed/homeLogic.ts";
import type { AttentionGap, EvidenceItem, IntelCard, RadarFeed } from "../lib/types.ts";

const ev = (over: Partial<EvidenceItem>): EvidenceItem => ({
  signal_id: null,
  title: "t",
  detail: null,
  source_type: "github",
  source_url: null,
  event_at: null,
  weight: null,
  ...over,
});

describe("epistemic display", () => {
  it("shows FACT / INFERRED / ANALYSIS and never presents unknown values as fact", () => {
    assert.equal(epistemicDisplay("FACT").text, "FACT");
    assert.equal(epistemicDisplay("INFERENCE").text, "INFERRED");
    assert.equal(epistemicDisplay("MODEL_OPINION").text, "ANALYSIS");
    assert.equal(epistemicDisplay("something").text, "ANALYSIS");
    assert.equal(epistemicDisplay(null).text, "ANALYSIS");
  });
});

describe("confidence", () => {
  it("normalizes backend strings", () => {
    assert.equal(confidenceLevel("HIGH CONFIDENCE"), "HIGH CONFIDENCE");
    assert.equal(confidenceLevel("medium"), "MEDIUM CONFIDENCE");
    assert.equal(confidenceLevel("EARLY SIGNAL"), "EARLY SIGNAL");
    assert.equal(confidenceLevel(""), null);
    assert.equal(confidenceLevel(undefined), null);
    assert.equal(confidenceLevel("bogus"), null);
  });
});

describe("source families", () => {
  it("maps source types to independent families", () => {
    assert.equal(familyOf("arxiv"), "research");
    assert.equal(familyOf("hackernews"), "community");
    assert.equal(familyOf("x"), "social");
    assert.equal(familyOf("jobs"), "hiring");
    assert.equal(familyOf("mystery"), "other");
    assert.deepEqual(familyLabels(["arxiv", "github", "hiring/web", "github"]), ["GitHub", "Research", "Hiring", "Web"]);
  });

  it("groups drawer evidence by family, newest first, deduped", () => {
    const items = [
      ev({ title: "a", source_url: "https://github.com/a", event_at: "2026-10-01T00:00:00Z" }),
      ev({ title: "b", source_type: "arxiv", source_url: "https://arxiv.org/abs/1" }),
      ev({ title: "c", source_url: "https://github.com/c", event_at: "2026-10-09T00:00:00Z" }),
      ev({ title: "a", source_url: "https://github.com/a", event_at: "2026-10-01T00:00:00Z" }),
    ];
    const g = groupByFamily(items);
    assert.deepEqual(g.map((x) => x.label), ["GitHub", "Research"]);
    assert.deepEqual(g[0].items.map((x) => x.title), ["c", "a"]);
    assert.equal(countSources(items), 3);
    assert.equal(sourcesLabel(1), "1 source");
    assert.equal(sourcesLabel(3), "3 sources");
  });
});

describe("data age", () => {
  const now = new Date("2026-10-20T12:00:00Z");
  it("formats compact ages and refuses the far future", () => {
    assert.equal(ageShort("2026-10-20T11:52:00Z", now), "8 min ago");
    assert.equal(ageShort("2026-10-19T17:00:00Z", now), "19 h ago");
    assert.equal(ageShort("2026-10-17T12:00:00Z", now), "3 d ago");
    assert.equal(ageShort("2026-10-20T11:59:30Z", now), "just now");
    assert.equal(ageShort("2026-10-21T12:00:00Z", now), null);
    assert.equal(ageShort(null, now), null);
  });

  it("lists observed families, marks unobserved ones and always surfaces X", () => {
    const parts = dataAgeParts(
      { updated_at: null, last_observed_at: null, age_days: null, families: { github: "2026-10-20T11:52:00Z", hiring: null } },
      now,
    );
    assert.deepEqual(
      parts.map((p) => `${p.label} ${p.value}`),
      ["GitHub 8 min ago", "Hiring unavailable", "X unavailable"],
    );
    assert.deepEqual(dataAgeParts({ updated_at: null, last_observed_at: null, age_days: null, families: {} }, now), []);
  });

  it("lists only person-applicable families for people (no Hiring / Web)", () => {
    const parts = dataAgeParts(
      {
        updated_at: null,
        last_observed_at: null,
        age_days: null,
        families: { github: "2026-10-20T11:52:00Z", research: null, hiring: null, web: null, community: null },
      },
      now,
      "person",
    );
    assert.deepEqual(
      parts.map((p) => `${p.label} ${p.value}`),
      ["GitHub 8 min ago", "Research unavailable", "HN unavailable", "X unavailable"],
    );
  });

  it("phrases the live header", () => {
    assert.equal(updatedAgo("2026-10-20T11:48:00Z", now), "Updated 12 minutes ago");
    assert.equal(updatedAgo("2026-10-20T11:59:00Z", now), "Updated 1 minute ago");
    assert.equal(updatedAgo(null, now), "Update time unknown");
  });
});

describe("attention + collision views", () => {
  const base: AttentionGap = {
    technical: 88,
    attention: 29,
    gap: 59,
    flagged: true,
    coverage: "partial",
    sources: [
      { key: "hn_points", label: "HN points", available: true },
      { key: "x_followers", label: "X followers", available: false },
    ],
  };
  it("falls back to legacy fields and derives availability", () => {
    const v = attentionView(base);
    assert.deepEqual([v.technical, v.attention, v.gap], [88, 29, 59]);
    assert.equal(v.socialAvailable, false);
    assert.deepEqual(v.unavailable, ["X followers"]);
  });
  it("never claims a gap when attention is unmeasured", () => {
    const v = attentionView({ ...base, attention: null, gap: 0 });
    assert.equal(v.attention, null);
    assert.equal(v.gap, null);
    const v2 = attentionView({ ...base, technical_score: 90, attention_score: null, gap_points: null, explanation: "x", social_available: true });
    assert.deepEqual([v2.technical, v2.attention, v2.gap, v2.explanation, v2.socialAvailable], [90, null, null, "x", true]);
  });
  it("uses backend collision labels when present, honest fallbacks otherwise", () => {
    const c = { families: ["arxiv", "github"], summary: "2 independent sources within 5 days" };
    assert.deepEqual(collisionView(c), { strength: null, families: ["GitHub", "Research"], sentence: c.summary });
    assert.deepEqual(collisionView({ ...c, strength: "HIGH", family_labels: ["Research", "GitHub"], sentence: "S" }), {
      strength: "HIGH",
      families: ["Research", "GitHub"],
      sentence: "S",
    });
  });
});

describe("feedback", () => {
  it("offers INTERESTING / NOT INTERESTING with the agreed reasons", () => {
    assert.deepEqual(
      FEEDBACK_ITEMS.map((o) => o.label),
      ["interesting", "strong_founder", "interesting_shift", "not_interesting", "already_known", "too_obvious", "wrong_thesis", "weak_team", "bad_data", "too_early"],
    );
    assert.equal(feedbackText("already_known"), "Already knew this");
    assert.equal(feedbackText("useful"), "Interesting");
    assert.equal(feedbackPolarity("strong_founder"), "positive");
    assert.equal(feedbackPolarity("weak_team"), "negative");
    assert.equal(legacyFeedbackLabel("interesting_shift"), "useful");
    assert.equal(legacyFeedbackLabel("too_early"), null);
  });

  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("retries a v2 label once with its legacy label when an older backend returns 422", async () => {
    process.env.BACKEND_URL = "http://api:8000";
    const bodies: unknown[] = [];
    globalThis.fetch = (async (_url: string | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      bodies.push(body);
      if (body.label === "strong_founder") return new Response('{"detail":"bad label"}', { status: 422 });
      return new Response('{"ok":true,"weights":{},"changed":{},"dismissed":false}', { status: 200 });
    }) as typeof fetch;
    const out = await postFeedback({ entity_type: "company", entity_id: "c1", label: "strong_founder" });
    assert.equal(out.ok, true);
    assert.deepEqual(bodies, [
      { entity_type: "company", entity_id: "c1", label: "strong_founder" },
      { entity_type: "company", entity_id: "c1", label: "useful", note: "v2:strong_founder" },
    ]);
  });

  it("does not retry legacy labels", async () => {
    process.env.BACKEND_URL = "http://api:8000";
    let calls = 0;
    globalThis.fetch = (async () => {
      calls++;
      return new Response('{"detail":"nope"}', { status: 422 });
    }) as typeof fetch;
    await assert.rejects(postFeedback({ entity_type: "company", entity_id: "c1", label: "too_early" }));
    assert.equal(calls, 1);
  });
});

describe("home logic", () => {
  it("parses the window filter (legacy ?new=1 → week)", () => {
    assert.equal(parseWindow("today"), "today");
    assert.equal(parseWindow(["week"]), "week");
    assert.equal(parseWindow("1"), "week");
    assert.equal(parseWindow("all"), null);
    assert.equal(parseWindow(undefined), null);
    assert.equal(windowHref(null), "/");
    assert.equal(windowHref("today"), "/?new=today");
  });

  it("writes the header status line for live data and snapshots", () => {
    const now = new Date("2026-10-20T12:00:00Z");
    assert.equal(
      homeStatusLine({ updated_at: "2026-10-20T11:45:00Z", data_as_of: "2026-10-20", is_snapshot: false, snapshot_age_days: null }, now),
      "Updated 15 minutes ago",
    );
    assert.equal(
      homeStatusLine({ updated_at: null, data_as_of: "2026-10-06", is_snapshot: true, snapshot_age_days: 14 }, now),
      "VERIFIED SNAPSHOT · collected Oct 6, 2026 · 14 days old",
    );
    assert.equal(
      homeStatusLine({ updated_at: null, data_as_of: null, is_snapshot: true, snapshot_age_days: null }, now),
      "VERIFIED SNAPSHOT · collected date unknown · age unknown",
    );
  });

  it("links change items to the right route", () => {
    assert.equal(changeHref({ kind: "company", entity: { type: "company", id: "1", slug: "acme", name: "Acme" } }), "/companies/acme");
    assert.equal(changeHref({ kind: "person", entity: { type: "person", id: "p", slug: null, name: "P" } }), "/founders/p");
    assert.equal(changeHref({ kind: "formation", entity: { type: "formation", id: "f", slug: "team-x", name: "X" } }), "/formation/team-x");
    assert.equal(changeHref({ kind: "research", entity: { type: "paper", id: "x", slug: null, name: "A paper" } }), "/search?q=A%20paper");
    assert.equal(changeHref({ kind: "strange", entity: null }), null);
  });

  it("windows by first-seen relative to the data day; unknown dates are excluded from NEW views", () => {
    assert.ok(inWindow("2026-10-20T03:00:00Z", "2026-10-20", "today"));
    assert.ok(!inWindow("2026-10-19T23:00:00Z", "2026-10-20", "today"));
    assert.ok(inWindow("2026-10-14T01:00:00Z", "2026-10-20", "week"));
    assert.ok(!inWindow("2026-10-13T23:00:00Z", "2026-10-20", "week"));
    assert.ok(!inWindow(null, "2026-10-20", "week"));
    assert.ok(inWindow(null, "2026-10-20", null));
  });

  it("interleaves kinds and caps the list", () => {
    assert.deepEqual(interleave<number | string>([[1, 2, 3], ["a"], [], ["x", "y"]], 5), [1, "a", "x", 2, "y"]);
    assert.deepEqual(interleave([[1, 2, 3]], 8), [1, 2, 3]);
  });

  it("composes a fallback home from legacy feeds without inventing counts", () => {
    const card = (id: string, families: string[]): IntelCard =>
      ({
        rank: 1,
        entity_type: "company",
        id,
        slug: id,
        name: id.toUpperCase(),
        kind_label: "Company",
        one_liner: null,
        score: 50,
        delta_7d: null,
        delta_30d: null,
        thesis_tags: [],
        why_surfaced: [ev({ title: "stars up", event_at: "2026-10-19T00:00:00Z" })],
        why_anti: [],
        why_anti_epistemic: "INFERENCE",
        attention_gap: null,
        collision: { score: 0.5, families, window_days: 42, span_days: 3, signal_count: 3, evidence: [], summary: "2 independent sources within 3 days" },
        sparkline: [],
        sparkline_label: null,
        first_seen_at: "2026-10-20T01:00:00Z",
        is_new: true,
        watched: false,
        is_demo: false,
        data_note: null,
      }) as IntelCard;
    const radar = {
      date: "2026-10-20",
      generated_at: "2026-10-20T08:00:00Z",
      counts: { emerging_companies: 1, founders: 0, strange_signals: 0, thesis_shifts: 0, new_entities_7d: 2, signals_24h: 5 },
      cards: [card("a", ["github", "arxiv"]), card("b", ["github"])],
      founders: [],
      strange_signals: [],
      thesis_shifts: [],
      data_freshness: {},
      demo_mode: false,
      snapshot_note: null,
      data_as_of: "2026-10-20",
      is_snapshot: false,
      snapshot_age_days: null,
    } satisfies RadarFeed;
    const home = composeHomeFallback(radar, null, null, null);
    assert.equal(home.summary.possible_formations, 0);
    assert.equal(home.summary.people_to_meet, 0);
    assert.equal(home.summary.companies_accelerating, 2);
    assert.equal(home.summary.signal_collisions, 1);
    assert.deepEqual(home.what_changed.map((c) => c.kind), ["collision", "company"]);
    assert.equal(home.what_changed[0].why_now, "2 independent sources within 3 days");
    assert.equal(home.what_changed[1].why_now, "", "single-family card does not borrow the collision sentence");
    assert.equal(home.what_changed[0].at, "2026-10-19T00:00:00Z");
    assert.equal(composeHomeFallback(radar, null, null, "today").companies.length, 2);
  });
});

describe("home API client", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });
  it("calls /api/home with the window", async () => {
    process.env.BACKEND_URL = "http://api:8000";
    const urls: string[] = [];
    globalThis.fetch = (async (url: string | URL) => {
      urls.push(String(url));
      return new Response("{}", { status: 200 });
    }) as typeof fetch;
    await getHome();
    await getHome({ new: "today" });
    assert.deepEqual(urls, ["http://api:8000/api/home", "http://api:8000/api/home?new=today"]);
  });
});

describe("collision gate", () => {
  it("shows a collision only with ≥2 families or a backend strength above LOW", async () => {
    const { isCollision } = await import("../lib/labels.ts");
    assert.equal(isCollision({ families: ["github"] }), false);
    assert.equal(isCollision({ families: [], strength: "LOW" }), false);
    assert.equal(isCollision({ families: [], strength: "MEDIUM" }), true);
    assert.equal(isCollision({ families: ["github", "arxiv"] }), true);
    assert.equal(isCollision(null), false);
  });

  it("says 'single source family' only when a single-family collision is stored", async () => {
    const { collisionFallback } = await import("../lib/labels.ts");
    assert.equal(collisionFallback({ families: ["github"] }), "Single source family");
    assert.equal(collisionFallback(null), null); // no stored collision → no claim about families
    assert.equal(collisionFallback({ families: [] }), null);
    assert.equal(collisionFallback({ families: ["github", "arxiv"] }), null); // a real collision renders instead
  });
});

describe("provider health (footer = /status)", () => {
  it("counts live providers excluding disabled ones and reports the disabled count", async () => {
    const { providerHealth, providerHealthLine } = await import("../lib/labels.ts");
    const ps = ["healthy", "degraded", "healthy", "healthy", "never_run", "disabled"].map((state) => ({ state }));
    assert.deepEqual(providerHealth(ps), { healthy: 3, live: 5, disabled: 1 });
    assert.equal(providerHealthLine(ps), "3/5 live providers healthy · 1 disabled");
    assert.equal(providerHealthLine([{ state: "healthy" }]), "1/1 live providers healthy");
  });
});
