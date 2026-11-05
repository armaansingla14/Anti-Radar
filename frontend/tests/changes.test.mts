/** Since last snapshot: labels, the comparable-only delta rule, honest badge tones, home counts and links. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  BADGE,
  badgeMeta,
  droppedLine,
  formationBadgeTitle,
  formationChangeHref,
  hasReport,
  homeCaveat,
  homeCounts,
  meetRankText,
  sinceLabel,
  sinceView,
} from "../components/changes/logic.ts";
import type { ChangesReport } from "../lib/types-changes.ts";

const list = (count: number) => ({ count, items: [] });

function report(over: Record<string, unknown> = {}): ChangesReport {
  return {
    available: true,
    reason: null,
    base_day: "2026-10-20",
    current_day: "2026-11-05",
    points: [],
    is_snapshot: true,
    new_companies: list(148),
    new_people: list(1763),
    new_repositories: list(130),
    new_papers: list(322),
    new_jobs: list(12),
    new_research: list(7),
    new_signals_total: 882,
    new_signals_by_family: [],
    score_changes: {
      comparable: true, reason: null, base_version: "2026.10-1", current_version: "2026.10-1", threshold: 5,
      compared: 47, unchanged: 31, not_reobserved: 173, not_comparable: 0, no_baseline: 148,
      accelerated: list(5), weakened: list(11),
    },
    momentum_acceleration: list(12),
    new_collisions: list(1),
    collisions_ended: list(0),
    formation: {
      available: true, reason: null, base_day: "2026-10-20", current_day: "2026-11-05", threshold: 5,
      counts: { NEW: 3, DROPPED: 2, STRENGTHENED: 1, WEAKENED: 0, UNCHANGED: 3 },
      new: [], dropped: [], strengthened: [], weakened: [],
    },
    meet: { base_week: "2026-W43", current_week: "2026-W45", entered: [], left: [], moved: [] },
    caveats: [],
    ...over,
  } as unknown as ChangesReport;
}

describe("since last snapshot", () => {
  it("labels the comparison by the base day", () => {
    assert.equal(sinceLabel("2026-10-20"), "since Oct 20");
    assert.equal(sinceLabel(null), null);
  });

  it("shows a score delta only when the scores are comparable", () => {
    const base = { base_day: "2026-10-20", current_day: "2026-11-05", base_score: 29, current_score: 13 };
    const v = sinceView({ ...base, delta: -16, comparable: true, reason: null });
    assert.equal(v.kind, "delta");
    assert.ok(v.kind === "delta" && v.tone === "down" && v.text.includes("since Oct 20"));
    const nc = sinceView({ ...base, delta: -16, comparable: false, reason: "not re-observed since 2026-10-20" });
    assert.equal(nc.kind, "not-compared");
    assert.ok(nc.kind === "not-compared" && !nc.text.includes("16"), "no number when not comparable");
    assert.equal(sinceView(null).kind, "none");
  });

  it("gives decline badges the same visual weight as gains", () => {
    assert.equal(BADGE.WEAKENED.weight, BADGE.STRENGTHENED.weight);
    assert.equal(BADGE.DROPPED.weight, "strong");
    assert.equal(badgeMeta("weakened").tone, "down");
    assert.equal(badgeMeta("SOMETHING_ELSE").weight, "quiet");
  });

  it("uses the unrounded current score and backend threshold in badge tooltips", () => {
    const t = formationBadgeTitle(
      { status: "STRENGTHENED", base_day: "2026-10-20", base_score: 40.4, base_tier: "early", current_score: 45.4, threshold: 5 },
      45, "early");
    assert.ok(t.includes("40.4 → 45.4"), t);
    const u = formationBadgeTitle(
      { status: "UNCHANGED", base_day: "2026-10-20", base_score: 30, base_tier: "early", current_score: 31, threshold: 4 },
      31, "early");
    assert.ok(u.includes("4-point threshold"), u);
  });

  it("builds the home counts, including declines, and nothing when unavailable", () => {
    const keys = homeCounts(report()).map((c) => [c.key, c.count]);
    assert.deepEqual(keys, [["companies", 148], ["people", 1763], ["formations", 3], ["accelerated", 5], ["weakened", 11],
      ["collisions", 1], ["hiring", 12]]);
    assert.deepEqual(homeCounts(report({ available: false })), []);
    assert.equal(hasReport(null), false);
    const noScores = report({ score_changes: { ...report().score_changes, comparable: false, reason: "versions differ" } });
    assert.ok(!homeCounts(noScores).some((c) => c.key === "accelerated" || c.key === "weakened"));
    assert.ok(homeCaveat(noScores).startsWith("Scores are not compared"));
    assert.ok(homeCaveat(report()).includes("173 companies were not re-observed"));
  });

  it("links dropped candidates to the anchor page by its own slug", () => {
    const dropped = { status: "DROPPED", slug: "person-simon-mo", kind: "person", anchor_type: "person", anchor_id: "x", anchor_slug: "simon-mo" } as const;
    assert.equal(formationChangeHref(dropped), "/founders/simon-mo");
    assert.equal(formationChangeHref({ ...dropped, status: "NEW" }), "/formation/person-simon-mo");
    assert.equal(droppedLine(report()), "2 candidates dropped since Oct 20");
    assert.equal(meetRankText({ base_rank: null, current_rank: 1 }), "entered at #1");
    assert.equal(meetRankText({ base_rank: 4, current_rank: null }), "was #4");
  });
});
