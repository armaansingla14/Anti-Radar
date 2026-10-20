/**
 * Formation Radar display logic: honest copy, self-reported labelling, the
 * relationship graph built from the relationships table, score coercion for the
 * shared Why drawer, URL state, and the API client paths.
 */
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { getFormation, getFormationDetail, getFormationScore } from "../lib/api.ts";
import { ALL_NAV, NAV_PRIMARY } from "../lib/routes.ts";
import {
  cleanAffiliations,
  convergenceStrength,
  familyWords,
  formationDeck,
  formationListHref,
  memberNames,
  memberSummary,
  parseFormationQuery,
  relationshipGraph,
  relationshipVerb,
  relationshipYears,
  sortRelationships,
  toAntiScore,
  watchTarget,
} from "../components/formation/logic.ts";
import type { FormationMember, RelationshipOut, ScoreComponent } from "../lib/types.ts";

const person = (id: string, name: string) => ({ type: "person", id, slug: `p-${id}`, name });
const rel = (id: string, kind: string, a: ReturnType<typeof person>, b: ReturnType<typeof person> | { type: string; id: string; slug: string | null; name: string }, extra: Partial<RelationshipOut> = {}): RelationshipOut => ({
  id,
  kind,
  epistemic: "VERIFIED",
  src: a,
  dst: b,
  first_seen_at: null,
  last_seen_at: null,
  evidence: [],
  ...extra,
});
const member = (id: string, name: string, extra: Partial<FormationMember> = {}): FormationMember => ({
  id,
  slug: `p-${id}`,
  name,
  headline: null,
  avatar_url: null,
  founder_score: 50,
  affiliations: [],
  is_demo: false,
  ...extra,
});

describe("navigation", () => {
  it("puts FORMATION second and MEET third in the primary nav", () => {
    assert.deepEqual(
      NAV_PRIMARY.map((n) => n.label.toUpperCase()),
      ["RADAR", "FORMATION", "MEET", "FOUNDERS", "COMPANIES", "THESIS", "WATCHLIST", "SEARCH"],
    );
  });

  it("keeps G-jump keys unique", () => {
    const keys = ALL_NAV.map((n) => n.jump).filter(Boolean);
    assert.equal(new Set(keys).size, keys.length);
  });
});

describe("formation copy", () => {
  it("is honest when nothing was detected", () => {
    assert.equal(formationDeck({ high: 0, emerging: 0, early: 0, total: 0 }), "No formation signals detected in this period.");
  });

  it("summarises tiers in prose without claiming founding", () => {
    const s = formationDeck({ high: 1, emerging: 3, early: 12, total: 16 });
    assert.equal(s, "16 possible formations in view: one high-confidence, three emerging and 12 early.");
    assert.doesNotMatch(s, /found/i);
    assert.equal(formationDeck({ high: 0, emerging: 1, early: 0, total: 1 }), "One possible formation in view: one emerging.");
  });

  it("orders source families canonically as words", () => {
    assert.deepEqual(familyWords(["social", "research", "github", "GitHub", "hiring"]), ["GitHub", "Research", "Hiring", "Social"]);
    assert.deepEqual(familyWords(["community", "web", "patents"]), ["Web", "Community", "Patents"]);
    // Scorer collision keys map onto display families.
    assert.deepEqual(familyWords(["x", "hiring/web", "arxiv", "hackernews", "github"]), ["GitHub", "Research", "Hiring", "Web", "Community", "Social"]);
  });

  it("dedupes affiliations and strips @ handles", () => {
    assert.deepEqual(cleanAffiliations(["@nvidia", "NVIDIA ", "CMU", "", "cmu", "Google"], 3), ["nvidia", "CMU", "Google"]);
  });

  it("builds the member line parts", () => {
    const card = { members: [member("a", "Ana"), member("b", "Raj")], previous_affiliations: ["NVIDIA", "CMU", "nvidia"] };
    assert.deepEqual(memberSummary(card), { count: "2 people", previously: ["NVIDIA", "CMU"] });
    assert.equal(memberSummary({ members: [member("a", "Ana")], previous_affiliations: [] }).count, "1 person");
    assert.equal(memberNames([member("a", "Ana"), member("b", "Raj"), member("c", "Li")]), "Ana, Raj and Li");
    assert.equal(memberNames([member("a", "A"), member("b", "B"), member("c", "C"), member("d", "D")]), "A, B, C and 1 other");
  });
});

describe("relationships", () => {
  const a = person("a", "Ana");
  const b = person("b", "Raj");
  const org = { type: "company", id: "org", slug: "acme", name: "Acme Robotics" };

  it("only FOUNDED says founded", () => {
    for (const k of ["WORKED_WITH", "COAUTHORED_WITH", "CONTRIBUTED_WITH", "MEMBER_OF", "AUTHORED", "CONTRIBUTED_TO"]) {
      assert.doesNotMatch(relationshipVerb(k), /found/i, k);
    }
    assert.equal(relationshipVerb("FOUNDED"), "founded");
    assert.equal(relationshipVerb("COAUTHORED_WITH"), "co-authored with");
  });

  it("renders year spans from evidence dates", () => {
    assert.equal(relationshipYears({ first_seen_at: "2023-02-01T00:00:00Z", last_seen_at: "2025-06-01T00:00:00Z" }), "2023–2025");
    assert.equal(relationshipYears({ first_seen_at: "2025-02-01", last_seen_at: "2025-06-01" }), "2025");
    assert.equal(relationshipYears({ first_seen_at: null, last_seen_at: null }), "");
  });

  it("sorts verified person ties first, then most recent", () => {
    const rels = [
      rel("1", "WORKED_WITH", a, b, { epistemic: "INFERRED", last_seen_at: "2026-01-01" }),
      rel("2", "MEMBER_OF", a, org, { last_seen_at: "2026-09-01" }),
      rel("3", "COAUTHORED_WITH", a, b, { last_seen_at: "2025-01-01" }),
    ];
    assert.deepEqual(sortRelationships(rels).map((r) => r.id), ["3", "2", "1"]);
  });

  it("builds a graph with the anchor, members, deduped edges and dashed inferred ties", () => {
    const rels = [
      rel("1", "COAUTHORED_WITH", a, b),
      rel("2", "COAUTHORED_WITH", b, a),
      rel("3", "WORKED_WITH", a, b, { epistemic: "INFERRED" }),
      rel("4", "MEMBER_OF", a, org),
      rel("5", "MEMBER_OF", a, a),
    ];
    const g = relationshipGraph(rels, org, [member("a", "Ana"), member("c", "Li")]);
    assert.deepEqual(g.nodes.map((n) => n.id).sort(), ["a", "b", "c", "org"]);
    assert.equal(g.edges.length, 3);
    assert.deepEqual(g.edges.map((e) => e.kind), ["coauthored_with", "prior_affiliation", "member_of"]);
  });
});

describe("convergence and score", () => {
  it("uses API strength, else derives it from the score", () => {
    assert.equal(convergenceStrength({ strength: "medium", score: 0.9 }), "MEDIUM");
    assert.equal(convergenceStrength({ strength: null, score: 0.7 }), "HIGH");
    assert.equal(convergenceStrength({ score: 0.1 }), "LOW");
  });

  const comps: ScoreComponent[] = [{ key: "founder_quality", label: "Founder quality", value: 18, max: 25, explanation: "x", raw: {}, evidence: [] }];

  it("fills neutral defaults so the shared Why drawer never invents signal", () => {
    const s = toAntiScore({ total: 71.6, components: comps });
    assert.ok(s);
    assert.equal(s.total, 72);
    assert.equal(s.feedback_adjustment, 0);
    assert.equal(s.attention_gap, null);
    assert.equal(s.collision, null);
    assert.equal(s.delta_30d, null);
  });

  it("falls back to card components when the endpoint is unavailable", () => {
    assert.equal(toAntiScore(null, { score: 64, components: comps })?.total, 64);
    assert.equal(toAntiScore(null), null);
  });

  it("watches the anchor only when it is a company or person", () => {
    assert.deepEqual(watchTarget({ type: "company", id: "x", slug: "acme", name: "Acme" }), { entityType: "company", entityId: "x" });
    assert.equal(watchTarget({ type: "repository", id: "r", slug: null, name: "a/b" }), null);
  });
});

describe("list URL state", () => {
  it("parses and rebuilds query params, dropping unknown tiers", () => {
    const q = parseFormationQuery({ tier: "emerging", theme: ["robotics"], demo: "0" });
    assert.deepEqual(q, { tier: "emerging", theme: "robotics", demo: false });
    assert.equal(formationListHref(q), "/formation?tier=emerging&theme=robotics&demo=0");
    assert.deepEqual(parseFormationQuery({ tier: "bogus" }), { tier: null, theme: null, demo: null });
    assert.equal(formationListHref({ tier: null, theme: null, demo: null }), "/formation");
  });
});

describe("formation API client", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  function capture(): string[] {
    const urls: string[] = [];
    globalThis.fetch = (async (url: string | URL) => {
      urls.push(String(url));
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    return urls;
  }

  it("hits /api/formation with filters and encodes slugs", async () => {
    process.env.BACKEND_URL = "http://api:8000";
    const urls = capture();
    await getFormation({ tier: "high", theme: "ai", include_demo: false });
    await getFormationDetail("acme robotics/x");
    await getFormationScore("abc");
    assert.equal(urls[0], "http://api:8000/api/formation?tier=high&theme=ai&include_demo=false");
    assert.equal(urls[1], "http://api:8000/api/formation/acme%20robotics%2Fx");
    assert.equal(urls[2], "http://api:8000/api/formation/abc/score");
  });
});
