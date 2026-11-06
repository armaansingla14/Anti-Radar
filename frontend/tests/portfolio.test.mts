/** Portfolio Pulse display logic: labels, funnel, proxies (never headcount), windows, private fields, filters, trace, tabs. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  actionLabel,
  compIndex,
  countsRow,
  decisionLabel,
  deltaText,
  eventFilterHref,
  eventFilterLinks,
  eventQueries,
  funnelLine,
  funnelSteps,
  materialityLabel,
  mergeEvents,
  metricName,
  needsAttentionCount,
  parseEventFilters,
  positionFacts,
  priorityLabel,
  proxyDescription,
  proxyHeadline,
  proxyIsNot,
  proxyKind,
  relationOf,
  reviewText,
  sinceRows,
  standaloneProxies,
  stripPrivate,
  thingsWorthKnowing,
  traceSteps,
  traceStateLabel,
  typeLabel,
  windowText,
} from "../components/portfolio/logic.ts";
import { effectiveTab, parseTab, tabHref, visibleTabs } from "../components/diligence/logic.ts";
import type { CompsMap, Funnel, PCompany, PDecision, PEvent, PortfolioIndexItem, Position } from "../lib/types-portfolio.ts";

const co = (slug: string, name = slug): PCompany => ({ id: `id-${slug}`, slug, name, website: null, domain: null, github_org: null, is_demo: false });
const funnel: Funnel = {
  raw_changes: 1215, candidates: 432, after_dedup: 284, material: 5, watch: 35, noise: 244, companies_surfaced: 5,
  day: "2026-11-06", window: { start: "2026-10-07", end: "2026-11-06" }, available: true, reason: null,
};
const position: Position = {
  portfolio_name: "Anti Fund", status: "active", publicly_disclosed: true, source_url: "https://antifund.com/", source_checked_at: "2026-11-06",
  investment_stage: "Series B-C", investment_year: 2025, sector: null, thesis_tags: [],
};
const ev = (id: string, slug: string, at: string, extra: Partial<PEvent> = {}): PEvent => ({
  id, company: co(slug), event_type: "PRODUCT", event_subtype: "product_launch", title: id, description: null, event_at: at, detected_at: null,
  source_urls: [], sources: [], confidence: 1, epistemic: "FACT", materiality: "WATCH", materiality_rule: "M4", materiality_reason: "r", novelty: null,
  impact_areas: [], why_it_matters: "w", why_it_matters_epistemic: "INFERENCE", suggested_review: "REVIEW", status: "NEW", reviewed_at: null,
  reviewed_by: null, raw_count: 1, families: [], relation: null, performance_claim: false, is_demo: false, ...extra,
});
const dec = (kind: string, actor = "system", at = "2026-11-06T00:00:00Z"): PDecision => ({ id: `${kind}-${actor}-${at}`, kind, actor, note: kind, event_id: "e", question_id: null, created_at: at });

describe("materiality, priority and action labels", () => {
  it("maps materiality and priority", () => {
    assert.equal(materialityLabel("material"), "MATERIAL");
    assert.equal(materialityLabel("WATCH"), "WATCH");
    assert.equal(materialityLabel("NOISE"), "NOISE");
    assert.equal(materialityLabel("bogus"), "UNCLASSIFIED");
    assert.equal(priorityLabel("HIGH"), "HIGH PRIORITY");
    assert.equal(priorityLabel(null), "NOT RANKED");
  });
  it("uses exactly the six review actions and never suggests contacting a founder", () => {
    assert.equal(actionLabel("UPDATE_COMPETITIVE_MAP"), "UPDATE COMPETITIVE MAP");
    assert.equal(actionLabel("RUN_DILIGENCE"), "RUN DILIGENCE");
    assert.equal(actionLabel("PREPARE_FOUNDER_QUESTIONS"), "PREPARE FOUNDER QUESTIONS");
    assert.equal(actionLabel("NO_ACTION"), "NO ACTION");
    assert.equal(actionLabel("CONTACT_FOUNDER"), "REVIEW");
    assert.equal(actionLabel(undefined), "REVIEW");
    for (const a of ["REVIEW", "MONITOR", "UPDATE_COMPETITIVE_MAP", "RUN_DILIGENCE", "PREPARE_FOUNDER_QUESTIONS", "NO_ACTION", "CONTACT_FOUNDER", "REACH_OUT"])
      assert.doesNotMatch(actionLabel(a), /contact|reach out|email|call/i);
    assert.equal(decisionLabel("NEEDS_FOLLOW_UP"), "NEEDS FOLLOW UP");
  });
  it("labels type.subtype readably", () => {
    assert.equal(typeLabel("PRODUCT", "product_launch"), "PRODUCT · product launch");
    assert.equal(typeLabel("RISK", null), "RISK");
  });
});

describe("counts row", () => {
  it("leads with need attention · material · watch, keeps technical/competitive/financing, adds other types only when nonzero", () => {
    const row = countsRow({ need_attention: 5, material: 5, watch: 29, noise: 244, technical: 1, competitive: 19, financing: 0, product: 11, talent: 1, commercial: 0, risk: 0 });
    assert.deepEqual(row.map((r) => r.key), ["need_attention", "material", "watch", "technical", "competitive", "financing", "product", "talent"]);
    assert.equal(row[2].value, 29);
    assert.match(row[1].title, /portfolio company/);
    assert.match(row.find((r) => r.key === "competitive")!.title, /34 material \+ watch/);
    assert.equal(row.find((r) => r.key === "competitive")!.href, "/portfolio/events?relation=COMPETITOR");
  });
});

describe("noise funnel", () => {
  it("formats the stored run, no invented numbers", () => {
    assert.equal(
      funnelLine(funnel),
      "1,215 raw changes observed → 432 candidate events → 284 after dedup → 5 material · 35 watch · 244 noise → 5 companies surfaced",
    );
    assert.equal(funnelSteps(funnel).length, 5);
    assert.equal(funnelSteps({ ...funnel, companies_surfaced: 1 })[4].label, "company surfaced");
  });
  it("says why when the run is unavailable", () => {
    assert.deepEqual(funnelSteps({ ...funnel, available: false }), []);
    assert.equal(funnelLine({ ...funnel, available: false, reason: "no portfolio run yet" }), "Funnel unavailable: no portfolio run yet");
    assert.match(funnelLine(null), /unavailable/);
  });
});

describe("public proxies", () => {
  const open = { metric: "proxy_open_technical_roles", value: 12, day: "2026-11-06", source_url: null, confidence: 0.9, proxy_label: "Public hiring signal: open technical roles on the public job board (not headcount)" };
  it("labels a proxy by the signal it is, never as headcount", () => {
    assert.equal(proxyHeadline(open), "HIRING SIGNAL · 12 open technical roles");
    assert.doesNotMatch(proxyHeadline(open), /headcount/i);
    assert.equal(proxyIsNot(open.proxy_label), "not headcount");
    assert.equal(proxyDescription(open.proxy_label), "open technical roles on the public job board");
    assert.doesNotMatch(metricName("headcount"), /headcount/i);
    assert.doesNotMatch(metricName("engineering_headcount"), /headcount/i);
  });
  it("derives the signal kind from the label or the metric", () => {
    assert.equal(proxyKind({ metric: "hn_points", proxy_label: "Public attention signal: Hacker News points" }), "ATTENTION SIGNAL");
    assert.equal(proxyKind({ metric: "releases", proxy_label: null }), "CODE SIGNAL");
    assert.equal(proxyKind({ metric: "proxy_customer_stories_30d", proxy_label: "Public customer stories published (not revenue)" }), "CUSTOMER STORIES");
    assert.equal(proxyKind({ metric: "federal_awards", proxy_label: null }), "AWARD SIGNAL");
  });
  it("keeps window halves out of the standalone list", () => {
    const names = standaloneProxies([
      { ...open, metric: "proxy_hn_points_30d" },
      { ...open, metric: "proxy_hn_points_prior_30d" },
      { ...open, metric: "proxy_new_technical_roles_14d" },
      { ...open, metric: "proxy_customer_stories_30d" },
      open,
    ]).map((p) => p.metric);
    assert.deepEqual(names, ["proxy_customer_stories_30d", "proxy_open_technical_roles"]);
  });
});

describe("comparable windows", () => {
  it("states both sides and the window", () => {
    const m = { current: 24, prior: 32, window: "30 days", comparable: true, reason: null, delta: -8 };
    assert.equal(windowText(m), "24 vs 32 · last 30 days vs the prior 30");
    assert.equal(deltaText(m), "−8");
    assert.equal(deltaText({ ...m, delta: 3 }), "+3");
    assert.equal(deltaText({ ...m, delta: 0 }), "±0");
  });
  it("says not compared and why", () => {
    const m = { current: null, prior: null, window: "14 days", comparable: false, reason: "one of the two windows was not observed", delta: null };
    assert.equal(windowText(m), "not compared: one of the two windows was not observed");
    assert.equal(windowText({ ...m, reason: null }), "not compared: both windows were not observed");
    assert.equal(deltaText(m), "");
  });
});

describe("private-field boundary", () => {
  it("drops private keys at any depth without mutating the input", () => {
    const raw = {
      investment_stage: "Seed",
      ownership: 0.1,
      cost_basis: 1,
      private_fields: { marks: 2 },
      nested: { board_seat: true, internal_notes: "x", revenue: 5, keep: 1, list: [{ customer_count: 3, ok: true }] },
    };
    const out = stripPrivate(raw) as Record<string, unknown>;
    assert.deepEqual(out, { investment_stage: "Seed", nested: { keep: 1, list: [{ ok: true }] } });
    assert.equal(raw.ownership, 0.1);
    assert.equal(stripPrivate(null), null);
  });
  it("stage and year are copied as public, unknowns stay UNKNOWN, round is never invented", () => {
    assert.deepEqual(positionFacts(position), { stage: "Series B-C", year: "2025", round: "UNKNOWN" });
    assert.deepEqual(positionFacts({ ...position, investment_stage: null, investment_year: null }), { stage: "UNKNOWN", year: "UNKNOWN", round: "UNKNOWN" });
    assert.deepEqual(positionFacts({ ...position, ownership: 0.2 } as Position), { stage: "Series B-C", year: "2025", round: "UNKNOWN" });
  });
});

describe("event filters", () => {
  it("parses and validates", () => {
    assert.deepEqual(parseEventFilters({}), { type: null, materiality: null, company: null, relation: null });
    assert.deepEqual(parseEventFilters({ type: "product", materiality: "watch", company: "Modal", relation: "self" }), {
      type: "PRODUCT", materiality: "WATCH", company: "modal", relation: "SELF",
    });
    assert.deepEqual(parseEventFilters({ type: "COMPETITIVE" }), { type: null, materiality: null, company: null, relation: "COMPETITOR" });
    assert.deepEqual(parseEventFilters({ type: "EVIL", materiality: "HUGE", company: "../x", relation: "FRIEND" }), { type: null, materiality: null, company: null, relation: null });
    assert.equal(parseEventFilters({ materiality: ["all", "x"] }).materiality, "ALL");
  });
  it("builds links that keep the other filters", () => {
    const f = parseEventFilters({ company: "modal" });
    assert.equal(eventFilterHref(f, "materiality", "MATERIAL"), "/portfolio/events?materiality=MATERIAL&company=modal");
    assert.equal(eventFilterHref(f, "company", null), "/portfolio/events");
    const links = eventFilterLinks({ companies: ["modal"] });
    assert.ok(links.includes("/portfolio/events?materiality=ALL"));
    assert.ok(links.includes("/portfolio/events?relation=COMPETITOR"));
    assert.ok(links.includes("/portfolio/events?company=modal"));
  });
  it("defaults to material + watch, ALL includes noise", () => {
    assert.deepEqual(eventQueries(parseEventFilters({})).map((q) => q.materiality), ["MATERIAL", "WATCH"]);
    assert.deepEqual(eventQueries(parseEventFilters({ materiality: "ALL" })).map((q) => q.materiality), [undefined]);
    assert.deepEqual(eventQueries(parseEventFilters({ materiality: "NOISE", type: "TECHNICAL" })), [{ type: "TECHNICAL", company: undefined, relation: undefined, materiality: "NOISE" }]);
  });
  it("merges lists newest first without duplicates", () => {
    const a = ev("a", "modal", "2026-10-01T00:00:00Z");
    const b = ev("b", "modal", "2026-11-01T00:00:00Z");
    assert.deepEqual(mergeEvents([[a], [b, a]]).map((e) => e.id), ["b", "a"]);
  });
});

describe("relations through comps", () => {
  const maps: CompsMap[] = [
    {
      company: co("cognition", "Cognition"),
      direct: [{ company: co("factory", "Factory"), comp_type: "DIRECT", why_comparable: "agents", source_url: "https://factory.ai", confidence: "HIGH", epistemic: "INFERENCE", recent_events: [], technical_momentum: null, anti_score: null }],
      emerging: [],
      adjacent: [],
    },
  ];
  const portfolio = new Set(["cognition"]);
  it("marks own events SELF and comp events with the portfolio company they belong to", () => {
    const comps = compIndex(maps);
    assert.equal(relationOf(ev("x", "cognition", "2026-11-01"), portfolio, comps).kind, "SELF");
    const r = relationOf(ev("y", "factory", "2026-11-01"), portfolio, comps);
    assert.equal(r.kind, "COMP");
    assert.equal(r.via[0].portfolio.name, "Cognition");
    assert.equal(relationOf(ev("z", "factory", "2026-11-01", { relation: "COMPETITOR" }), portfolio, comps).kind, "COMPETITOR");
    assert.equal(relationOf(ev("w", "nobody", "2026-11-01"), portfolio, comps).kind, "UNKNOWN");
  });
});

describe("decision trace", () => {
  it("shows system steps and leaves human steps awaiting review — never fabricated", () => {
    const steps = traceSteps([dec("detected"), dec("classified"), dec("question_raised")], "RUN_DILIGENCE");
    assert.deepEqual(steps.map((s) => s.key), ["EVENT", "REVIEW", "QUESTION", "ACTION"]);
    assert.deepEqual(steps.map((s) => s.state), ["system", "awaiting", "system", "awaiting"]);
    assert.equal(steps[0].lines.length, 2);
    assert.match(steps[1].placeholder!, /not reviewed/i);
    assert.match(steps[3].placeholder!, /no action taken.*RUN DILIGENCE/);
    assert.equal(traceStateLabel("awaiting"), "AWAITING REVIEW");
  });
  it("a system line can never fill a human step", () => {
    const steps = traceSteps([dec("reviewed", "system"), dec("diligence_requested", "system")]);
    assert.equal(steps[1].state, "awaiting");
    assert.equal(steps[3].state, "awaiting");
  });
  it("real human steps turn the step human", () => {
    const steps = traceSteps([dec("reviewed", "jane"), dec("question_answered", "jane"), dec("diligence_requested", "jane")]);
    assert.deepEqual(steps.map((s) => s.state), ["system", "human", "human", "human"]);
    assert.equal(reviewText(null), "Not reviewed");
    assert.equal(reviewText({ id: "r", decision: "MARK_REVIEWED", note: "", reviewer: "jane", created_at: null }), "MARK REVIEWED by jane");
  });
});

describe("index helpers", () => {
  const item = (slug: string, priority: PortfolioIndexItem["priority"], material = 0, watch = 0): PortfolioIndexItem => ({
    company: co(slug), position, material, watch, noise: 1, open_questions: 0, priority,
  });
  it("counts HIGH and MEDIUM as needing attention", () => {
    assert.equal(needsAttentionCount([item("a", "HIGH"), item("b", "MEDIUM"), item("c", "LOW"), item("d", null)]), 2);
    assert.equal(needsAttentionCount(null), 0);
  });
  it("lists every portfolio company, busiest first, zero rows kept", () => {
    const rows = sinceRows([item("quiet", null), item("busy", "MEDIUM", 1, 2)], [{ company: co("busy"), since: "2026-10-07", reviewed: false, material: 1, watch: 6 }]);
    assert.deepEqual(rows.map((r) => [r.company.slug, r.material, r.watch]), [["busy", 1, 6], ["quiet", 0, 0]]);
    assert.equal(rows[1].reviewed, false);
  });
  it("five things", () => {
    assert.equal(thingsWorthKnowing(5), "Five things worth knowing");
    assert.equal(thingsWorthKnowing(1), "One thing worth knowing");
  });
});

describe("company tabs", () => {
  it("RADAR replaces WHY NOW and the old name still resolves", () => {
    assert.equal(parseTab("why-now"), "radar");
    assert.equal(parseTab("WHY_NOW"), "radar");
    assert.equal(parseTab("radar"), "radar");
    assert.equal(tabHref("modal", "radar"), "/companies/modal?tab=radar");
  });
  it("PORTFOLIO and COMPS appear only for portfolio companies", () => {
    assert.deepEqual(visibleTabs().map((t) => t.key), ["overview", "radar", "diligence", "relationships", "reports"]);
    assert.deepEqual(visibleTabs({ portfolio: true, comps: true }).map((t) => t.label.toUpperCase()), [
      "OVERVIEW", "RADAR", "DILIGENCE", "PORTFOLIO", "COMPS", "RELATIONSHIPS", "REPORTS",
    ]);
    assert.equal(effectiveTab(parseTab("portfolio"), {}), "overview");
    assert.equal(effectiveTab(parseTab("portfolio"), { portfolio: true }), "portfolio");
    assert.equal(effectiveTab(parseTab("comps"), { portfolio: true }), "overview");
  });
});
