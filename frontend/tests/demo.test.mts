/**
 * /demo plan: scenes are resolved from data (never hard-coded names), ten scenes in order, and each pick
 * follows its rule — top candidate by precise score, strongest verified lab contributor (not the top
 * candidate's anchor), attention gap preferring formation anchors, existing briefs before live runs.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { buildPlan, fmtDay, labMembers, pickAttentionCompany, pickBrief, preciseScore, rankCandidates } from "../components/demo/plan.ts";
import type { CompanySummary, FormationCard, FormationFeed, MeetCard, MeetFeed, ReportOut } from "../lib/types.ts";

const comp = (key: string, value: number, raw: Record<string, unknown> = {}) => ({ key, label: key, value, max: 25, explanation: "", raw, evidence: [] });

function card(slug: string, parts: number[], extra: Partial<FormationCard> = {}): FormationCard {
  const total = parts.reduce((a, b) => a + b, 0);
  return {
    id: `id-${slug}`,
    slug,
    kind: "person",
    title: `${slug} — possible formation`,
    label: "POSSIBLE FORMATION",
    tier: "early",
    score: Math.round(total),
    components: parts.map((v, i) => comp(`c${i}`, v)),
    theme: null,
    theme_label: null,
    subtopic_label: null,
    members: [],
    previous_affiliations: [],
    independent_signals: 1,
    families: ["github"],
    first_detected_at: null,
    latest_activity_at: null,
    why_now: [],
    anchor: { type: "person", id: `p-${slug}`, slug, name: slug },
    is_demo: false,
    ...extra,
  } as FormationCard;
}

const feed = (cards: FormationCard[]): FormationFeed => ({ as_of: "2026-10-20", is_snapshot: true, counts: { high: 0, emerging: 0, early: cards.length, total: cards.length }, high: [], emerging: [], early: cards });

function labCard(slug: string, members: { id: string; slug: string; name: string; commits: number[] }[], anchor?: FormationCard["anchor"]): FormationCard {
  return card(slug, [10], {
    members: members.map((m) => ({ id: m.id, slug: m.slug, name: m.name, headline: null, avatar_url: null, founder_score: 40, affiliations: [], is_demo: false })),
    components: [
      comp("founder_quality", 10, {
        lab_members: members.map((m) => ({
          person_id: m.id,
          name: m.name,
          lab_contributions: m.commits.map((n, i) => ({ full_name: `lab/repo${i}`, commits: n })),
        })),
      }),
    ],
    ...(anchor ? { anchor } : {}),
  });
}

const company = (slug: string, gap: number | null, score: number, extra: Partial<CompanySummary> = {}): CompanySummary =>
  ({ id: `c-${slug}`, slug, name: slug, one_liner: null, kind: "company", stage: "unknown", score, delta_7d: null, thesis_tags: [], attention_gap: gap, collision: null, stars_30d: null, first_seen_at: null, website: null, github_org: null, is_demo: false, watched: false, ...extra }) as CompanySummary;

const report = (id: string, kind: string, entity_id: string | null, created_at: string, extra: Partial<ReportOut> = {}): ReportOut =>
  ({ id, kind, title: id, entity_type: "company", entity_id, entity_slug: null, body: {}, markdown: "", sources: [], model: null, generator: "template", status: "complete", cost_usd: 0, created_at, is_demo: false, ...extra }) as ReportOut;

describe("demo plan", () => {
  it("ranks candidates by the precise component sum, ignoring demo rows", () => {
    const a = card("a", [20, 20, 10.8]); // 50.8 → rounds to 51
    const b = card("b", [20, 20, 11.1]); // 51.1
    const fake = card("fake", [40, 40], { is_demo: true });
    assert.equal(preciseScore(a), 50.8);
    assert.deepEqual(rankCandidates(feed([a, b, fake])).map((c) => c.slug), ["b", "a"]);
  });

  it("finds verified lab contributors with slugs from the card members, most commits first", () => {
    const team = labCard("team-x", [
      { id: "p1", slug: "one", name: "One", commits: [700, 84] },
      { id: "p2", slug: "two", name: "Two", commits: [200] },
    ]);
    const lab = labMembers([team]);
    assert.deepEqual(lab.map((m) => [m.slug, m.commits, m.top_repo]), [["one", 784, "lab/repo0"], ["two", 200, "lab/repo0"]]);
  });

  it("prefers an attention-gap company that is also a formation anchor, then score ≥ 40", () => {
    const anchorCard = card("team-y", [10], { anchor: { type: "company", id: "c-y", slug: "y", name: "y" } });
    const list = [company("noise", 95, 20), company("solid", 80, 45), company("y", 70, 30)];
    assert.equal(pickAttentionCompany(list, [anchorCard])?.slug, "y");
    assert.equal(pickAttentionCompany(list, [])?.slug, "solid");
    assert.equal(pickAttentionCompany([company("z", null, 50)], []), null);
  });

  it("opens an existing Anti Brief before investigating live", () => {
    const c = company("y", 70, 30);
    assert.deepEqual(pickBrief([report("r1", "brief", "c-y", "2026-10-20T10:00:00Z"), report("r2", "brief", "c-y", "2026-10-20T11:00:00Z")], c), {
      report_id: "r2",
      company_slug: "y",
      mode: "existing",
    });
    assert.equal(pickBrief([report("r3", "brief", "other", "2026-10-20T10:00:00Z")], c)?.mode, "investigate");
    assert.equal(pickBrief([report("r3", "brief", "other", "2026-10-20T10:00:00Z")], null)?.report_id, "r3");
    assert.equal(pickBrief([], null)?.mode, "none");
  });

  it("builds ten scenes in order from the data, with the meeting brief reached by clicking when none exists", () => {
    const top = card("person-k", [30, 21.1], { anchor: { type: "person", id: "pk", slug: "k", name: "K" } });
    const team = labCard("team-s", [{ id: "pz", slug: "z", name: "Z", commits: [784] }], { type: "company", id: "c-s", slug: "s", name: "s" });
    const meetCard = { rank: 1, priority: "MEDIUM", person: { id: "pk", slug: "k", name: "K" }, brief_report_id: null } as unknown as MeetCard;
    const meet = { week: "2026-W43", generated_at: null, as_of: null, people: [meetCard], history_weeks: [] } as MeetFeed;
    const plan = buildPlan({ home: null, formation: feed([team, top]), meet, companies: [company("s", 60, 41)], briefs: [], meetingBriefs: [] });
    assert.deepEqual([...new Set(plan.steps.map((s) => s.scene))], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    assert.equal(plan.picks.candidate?.slug, "person-k");
    assert.equal(plan.picks.person?.slug, "z", "second thread: not the top candidate's anchor");
    const brief = plan.steps.find((s) => s.title === "Meeting brief")!;
    assert.equal(brief.url, null);
    assert.match(brief.click ?? "", /meeting-brief/);
    const anti = plan.steps.find((s) => s.scene === 9)!;
    assert.equal(anti.settle, "investigation");
    assert.equal(anti.url, "/companies/s");
    assert.equal(plan.steps.find((s) => s.scene_key === "collision")?.focus, "#convergence");
  });

  it("degrades to list pages on an empty dataset", () => {
    const plan = buildPlan({ home: null, formation: null, meet: null, companies: [], briefs: [], meetingBriefs: [] });
    assert.deepEqual([...new Set(plan.steps.map((s) => s.scene))], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    assert.equal(plan.picks.candidate, null);
    assert.equal(fmtDay("2026-10-20"), "20 Oct 2026");
  });
});
