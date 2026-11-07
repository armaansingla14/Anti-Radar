/**
 * /demo/portfolio plan: picks follow their rules over whatever data is loaded (no hard-coded names or numbers),
 * nine scenes in order, captions built from the payloads, and every scene degrades when its entity is missing.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildPortfolioPlan,
  clip,
  failureCounts,
  pickComp,
  pickCompany,
  pickEventId,
  pickHandoffCard,
  pickHandoffClaim,
  pickHandoffEvent,
  PORTFOLIO_SCENE_COUNT,
  type PortfolioInputs,
} from "../components/demo/portfolioPlan.ts";
import type { AttentionCard, Comp, CompsMap, EventDetail, PCompany, PEvent, PortfolioCompany, Pulse, WhatChangedItem } from "../lib/types-portfolio.ts";
import type { DiligenceClaim, DiligenceWorkspace } from "../lib/types-diligence.ts";
import type { AgentsOverview, FailureOut } from "../lib/types-agents.ts";

const co = (slug: string): PCompany => ({ id: `id-${slug}`, slug, name: slug.toUpperCase(), website: null, domain: null, github_org: null, is_demo: false });
const wc = (id: string, relation: string, materiality: "MATERIAL" | "WATCH" = "WATCH", subtype = "product_launch"): WhatChangedItem => ({
  event_id: id, title: `t-${id}`, materiality, relation, subject: relation === "SELF" ? "SELF" : "COMP", event_type: "PRODUCT", subtype, event_at: "2026-11-01T00:00:00Z",
});
const card = (slug: string, items: WhatChangedItem[], action = "REVIEW", material: string[] = []): AttentionCard => ({
  company: co(slug), priority: "MEDIUM", score: 10, why_surfaced: `${slug} why`, what_changed: items, material_events: material,
  what_this_could_mean: "m", what_this_could_mean_epistemic: "INFERENCE", questions: [], suggested_action: action as AttentionCard["suggested_action"],
  action_reason: `${slug} reason`, sources: [], counts: {},
});
const ev = (id: string, slug: string, extra: Partial<PEvent> = {}): PEvent => ({
  id, company: co(slug), event_type: "TECHNICAL", event_subtype: "performance_claim", title: `title ${id}`, description: null, event_at: "2026-10-19T00:00:00Z",
  detected_at: null, source_urls: [`https://${slug}.com/${id}`], sources: [{ url: `https://${slug}.com/${id}`, title: "t", source_type: "website", family: "web", publisher: "company", observed_at: null }],
  confidence: 1, epistemic: "FACT", materiality: "MATERIAL", materiality_rule: "M2", materiality_reason: "announced by the company", novelty: null, impact_areas: [],
  why_it_matters: "It matters.", why_it_matters_epistemic: "INFERENCE", suggested_review: "REVIEW", status: "NEW", reviewed_at: null, reviewed_by: null,
  raw_count: 1, families: ["web"], relation: null, performance_claim: false, is_demo: false, affects: [], ...extra,
});
const comp = (slug: string, comp_type = "DIRECT", events: PEvent[] = []): Comp => ({
  company: co(slug), comp_type, why_comparable: `${slug} overlaps`, source_url: `https://${slug}.com`, confidence: "HIGH", epistemic: "INFERENCE", recent_events: events, technical_momentum: null, anti_score: null,
});
const view = (slug: string, events: PEvent[] = [], competitor: PEvent[] = []): PortfolioCompany =>
  ({
    company: co(slug), position: { portfolio_name: "Anti Fund", status: "active", publicly_disclosed: true, source_url: null, source_checked_at: null, investment_stage: "Seed", investment_year: 2025, sector: null, thesis_tags: [] },
    events, competitor_events: competitor, noise_count: 0, metrics: [], proxies: [], comps: [], questions: [], last_review: null, next_review: null, decision_trace: [],
    data_age: null, report_id: null, diligence: { status: "NOT_RUN", run_id: null }, needs_attention: null,
  }) as PortfolioCompany;
const claim = (ordinal: number, claim_type: string, source_url: string | null): DiligenceClaim => ({ ordinal, text: `claim ${ordinal}`, claim_type, source_url, status: "UNVERIFIED", test_plan: { metrics: ["m"] } }) as unknown as DiligenceClaim;
const failure = (number: number, extra: Partial<FailureOut> = {}): FailureOut =>
  ({ number, claim_text: `failure ${number}`, agent: "PORTFOLIO ANALYST", origin: "documented", status: "FIXED", regression_case: null, regression_test: "tests/x.py::t", ...extra }) as FailureOut;

const pulse = (cards: AttentionCard[]): Pulse =>
  ({
    day: "2026-11-06",
    counts: { need_attention: cards.length, material: 6, watch: 29 },
    funnel: { raw_changes: 1215, candidates: 432, after_dedup: 284, material: 6, watch: 35, noise: 242, companies_surfaced: 5, day: "2026-11-06", window: null, available: true, reason: null },
    needs_attention: cards,
    material_events: [],
    movers: [],
    since_last_review: [],
    monitoring: { tracked: 59, portfolio_companies: 16, comps: 43, fresh: 59, stale: 0, never_observed: 0, stale_after_days: 7, providers: [], events_updated_at: null, data_as_of: "2026-11-06" },
    brief_id: null,
  }) as Pulse;

describe("pickers", () => {
  it("picks the first needs-attention company with ≥3 events including a competitor's and a material one", () => {
    const a = card("a", [wc("1", "SELF"), wc("2", "SELF")]);
    const b = card("b", [wc("3", "SELF"), wc("4", "SELF"), wc("5", "SELF")], "REVIEW", ["3"]);
    const c = card("c", [wc("6", "COMPETITOR", "MATERIAL"), wc("7", "SELF"), wc("8", "COMPETITOR")], "UPDATE_COMPETITIVE_MAP", ["6"]);
    assert.equal(pickCompany([a, b, c])?.company.slug, "c");
    assert.equal(pickCompany([a, b])?.company.slug, "a");
    assert.equal(pickCompany([]), null);
    assert.equal(pickEventId(c), "6");
    assert.equal(pickEventId(card("d", [wc("9", "SELF")])), "9");
    assert.equal(pickEventId(null), null);
  });
  it("picks the comp behind the event, else the busiest comp", () => {
    const map: CompsMap = { company: co("c"), direct: [comp("quiet"), comp("busy", "DIRECT", [ev("x", "busy"), ev("y", "busy")])], emerging: [], adjacent: [comp("subject", "ADJACENT")] };
    assert.equal(pickComp(map, "subject")?.company.slug, "subject");
    assert.equal(pickComp(map, null)?.company.slug, "busy");
    assert.equal(pickComp(null, "x"), null);
  });
  it("RUN DILIGENCE handoff: card, its event and the claim it states", () => {
    const cards = [card("a", []), card("m", [], "RUN_DILIGENCE")];
    assert.equal(pickHandoffCard(cards)?.company.slug, "m");
    assert.equal(pickHandoffCard([card("a", [])]), null);
    const perf = ev("p", "m", { suggested_review: "RUN_DILIGENCE", performance_claim: true });
    assert.equal(pickHandoffEvent(view("m", [ev("q", "m"), perf]))?.id, "p");
    assert.equal(pickHandoffEvent(view("m", [ev("q", "m")])), null);
    const ws = { claims: [claim(2, "PERFORMANCE", "https://other"), claim(1, "SCALABILITY", "https://m.com/p")], run: {} } as unknown as DiligenceWorkspace;
    assert.equal(pickHandoffClaim(ws, perf)?.ordinal, 1);
    assert.equal(pickHandoffClaim(ws, ev("z", "m"))?.ordinal, 2);
    assert.equal(pickHandoffClaim(null, perf), null);
  });
  it("counts failures and the ones pinned by a regression test", () => {
    assert.deepEqual(failureCounts([failure(1), failure(2, { regression_test: null, regression_case: null }), failure(3, { origin: "evaluator" })]), { total: 3, pinned: 2, documented: 2 });
  });
});

const full = (): PortfolioInputs => {
  const items = [wc("e1", "COMPETITOR", "MATERIAL", "acquisition"), wc("e2", "SELF"), wc("e3", "COMPETITOR")];
  const cog = card("cog", items, "UPDATE_COMPETITIVE_MAP", ["e1"]);
  const mod = card("mod", [wc("m1", "SELF", "MATERIAL")], "RUN_DILIGENCE", ["m1"]);
  const e1 = ev("e1", "fac", { affects: [{ company_slug: "cog", company_name: "COG", relation: "COMPETITOR", comp_type: "DIRECT", impact_reason: "r", materiality_for_company: "MATERIAL" }] });
  const perf = ev("perf", "mod", { suggested_review: "RUN_DILIGENCE", performance_claim: true, materiality: "WATCH", materiality_rule: "M7" });
  const detail = (e: PEvent): EventDetail => ({ event: e, impacts: [], questions: [], decision_trace: [], suggested_action: e.suggested_review, diligence: null, related_comps: [] });
  return {
    pulse: pulse([cog, mod]),
    company: view("cog", [ev("e2", "cog")], [e1]),
    event: detail(e1),
    comps: [{ company: co("cog"), direct: [comp("fac", "DIRECT", [e1])], emerging: [], adjacent: [] }],
    handoff: {
      company: view("mod", [perf]),
      event: detail(perf),
      workspace: { claims: [claim(1, "PERFORMANCE", "https://mod.com/perf")], run: { id: "r" }, header: { date: "2026-11-06", sources_analyzed: 40 }, definitions: {} } as unknown as DiligenceWorkspace,
    },
    brief: { id: "b", date: "2026-11-06", title: "t", markdown: "", sources: [], generator: "template", created_at: null, body: { date: "2026-11-06", five_things: [{}, {}, {}, {}, {}], needs_attention: [{}, {}] } } as unknown as PortfolioInputs["brief"],
    agents: { agents: [{ key: "portfolio", name: "PORTFOLIO ANALYST", runs: 11, metrics: {} }], regression: { passing: 24, cases_total: 24 } } as unknown as AgentsOverview,
    failures: [failure(21), failure(34)],
    isSnapshot: true,
  };
};

describe("captions", () => {
  it("clips at a word boundary without double punctuation", () => {
    assert.equal(clip("short", 10), "short");
    assert.equal(clip("Agent-native software development platform with autonomous coding agents", 60), "Agent-native software development platform with autonomous…");
    assert.equal(clip("one two three, four", 15), "one two three…");
  });
});

describe("buildPortfolioPlan", () => {
  it("resolves nine scenes in order from real payloads, with captions from the data", () => {
    const plan = buildPortfolioPlan(full());
    assert.equal(plan.steps[0].scene_key, "title");
    assert.equal(plan.steps.at(-1)!.scene, PORTFOLIO_SCENE_COUNT);
    assert.deepEqual([...new Set(plan.steps.slice(1).map((s) => s.scene))], [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const by = (k: string) => plan.steps.filter((s) => s.scene_key === k);
    assert.match(by("pulse")[0].caption, /2 companies need attention · 6 material events · 29 to watch, across 16 portfolio companies/);
    assert.match(by("attention")[0].caption, /^2 companies need attention\. First: COG/);
    assert.equal(by("company")[0].url, "/portfolio/cog");
    assert.equal(by("company")[0].focus, "#competitive-changes");
    assert.equal(by("event")[0].url, "/portfolio/events/e1");
    assert.match(by("event")[0].caption, /MATERIAL by M2.*Reaches COG \(direct\)/);
    assert.equal(by("comps")[0].url, "/portfolio/comps#comps-cog");
    assert.equal(by("comps")[0].match, "/portfolio/comps");
    assert.equal(by("comps")[0].focus, '#comps-cog [data-comp="fac"]');
    for (const st of plan.steps) {
      assert.ok(st.caption.length <= 210, `caption fits the presenter bar: ${st.caption}`);
      assert.doesNotMatch(st.caption, /…\.|\.\./, "no doubled punctuation");
    }
    assert.match(by("comps")[0].caption, /^FAC is a direct comp of COG/);
    const d = by("diligence");
    assert.deepEqual(d.map((s) => s.url), ["/portfolio/events/perf", "/diligence/mod", "/diligence/mod/claims/1"]);
    assert.equal(d[1].click, '#action [data-demo="open-diligence"]');
    assert.match(d[1].caption, /does not run diligence/);
    for (const s of d) assert.doesNotMatch(s.caption, /\bran diligence\b|running diligence/i);
    assert.match(by("brief")[0].caption, /five things worth knowing, 2 companies/);
    assert.match(by("brief")[1].caption, /^1,215 raw changes observed → 432 candidate events/);
    assert.match(by("agents")[0].caption, /PORTFOLIO ANALYST is evaluated like every other agent: 11 runs/);
    assert.match(by("agents")[1].caption, /^2 real portfolio failures found by hand audit, each pinned/);
    assert.equal(by("agents")[1].url, "/agents/failures?agent=PORTFOLIO%20ANALYST");
    assert.deepEqual(plan.picks.handoff, { slug: "mod", name: "MOD", event_id: "perf", claim: 1, workspace: true });
    assert.deepEqual(plan.counts, { tracked: 59, raw_changes: 1215, material: 6, failures: 2, pinned: 2 });
    assert.equal(plan.is_snapshot, true);
  });

  it("degrades every scene to a list page when its entity is missing", () => {
    const plan = buildPortfolioPlan({ pulse: null, company: null, event: null, comps: null, handoff: { company: null, event: null, workspace: null }, brief: null, agents: null, failures: [] });
    assert.deepEqual([...new Set(plan.steps.slice(1).map((s) => s.scene))], [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    for (const s of plan.steps.slice(1, -1)) {
      assert.ok(s.url, `scene ${s.scene} has a page`);
      assert.ok(s.ready, `scene ${s.scene} has a ready selector`);
    }
    assert.equal(plan.steps.find((s) => s.scene === 6)!.url, "/diligence");
    assert.equal(plan.steps.find((s) => s.scene === 4)!.url, "/portfolio/events");
    assert.deepEqual(plan.counts, { tracked: 0, raw_changes: 0, material: 0, failures: 0, pinned: 0 });
  });

  it("without a workspace the handoff stops at the event (no claim of a run)", () => {
    const inp = full();
    inp.handoff.workspace = null;
    const d = buildPortfolioPlan(inp).steps.filter((s) => s.scene_key === "diligence");
    assert.deepEqual(d.map((s) => s.url), ["/portfolio/events/perf"]);
  });
});
