/**
 * "Who should we meet this week" + meeting brief: ISO-week handling, the week
 * selector, honest copy and provenance labels, the brief action state machine,
 * meeting-brief body parsing, and the API client paths/bodies.
 */
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";
import { getMeet, getReports, postMeetingBrief, refreshMeet } from "../lib/api.ts";
import { NAV_PRIMARY } from "../lib/routes.ts";
import {
  affiliationParts,
  briefAction,
  briefErrorText,
  citedSourceIds,
  confidenceTone,
  formationHref,
  isIsoWeek,
  isMeetingBrief,
  isoWeekFor,
  MEETING_BRIEF_SECTIONS,
  meetDeck,
  parseWeekParam,
  priorityTone,
  toStrings,
  weekMonday,
  weekOptions,
  weekRange,
  weekShort,
} from "../components/meet/logic.ts";

describe("ISO weeks", () => {
  it("validates week strings including 53-week years", () => {
    assert.ok(isIsoWeek("2026-W43"));
    assert.ok(isIsoWeek("2026-W53")); // 2026 has 53 ISO weeks (Dec 31 2026 is a Thursday)
    assert.ok(!isIsoWeek("2025-W53"));
    assert.ok(!isIsoWeek("2026-W00"));
    assert.ok(!isIsoWeek("2026-43"));
    assert.ok(!isIsoWeek("current"));
    assert.ok(!isIsoWeek(null));
  });

  it("computes the Monday and a readable range", () => {
    assert.equal(weekMonday("2026-W43")?.toISOString().slice(0, 10), "2026-10-19");
    assert.equal(weekMonday("2026-W01")?.toISOString().slice(0, 10), "2025-12-29");
    assert.equal(weekRange("2026-W43"), "Oct 19 – 25, 2026");
    assert.equal(weekRange("2026-W44"), "Oct 26 – Nov 1, 2026");
    assert.equal(weekRange("2026-W53"), "Dec 28, 2026 – Jan 3, 2027");
    assert.equal(weekRange("nope"), null);
    assert.equal(weekShort("2026-W07"), "Week 7");
  });

  it("uses America/New_York for the current week", () => {
    // Monday 02:00 UTC is still Sunday evening in New York → previous ISO week.
    assert.equal(isoWeekFor(new Date("2026-10-26T02:00:00Z")), "2026-W43");
    assert.equal(isoWeekFor(new Date("2026-10-26T02:00:00Z"), "UTC"), "2026-W44");
    assert.equal(isoWeekFor(new Date("2026-10-20T15:00:00Z")), "2026-W43");
  });

  it("parses ?week= safely", () => {
    assert.equal(parseWeekParam(undefined), "current");
    assert.equal(parseWeekParam("2026-W42"), "2026-W42");
    assert.equal(parseWeekParam(["2026-W41", "x"]), "2026-W41");
    assert.equal(parseWeekParam("../../etc"), "current");
  });
});

describe("week selector", () => {
  it("puts this week first, dedupes, sorts history newest first, marks selection", () => {
    const opts = weekOptions({ week: "2026-W43", history_weeks: ["2026-W41", "2026-W43", "2026-W42"] }, "current");
    assert.deepEqual(
      opts.map((o) => [o.week, o.label, o.href, o.selected]),
      [
        ["2026-W43", "This week", "/meet", true],
        ["2026-W42", "Week 42", "/meet?week=2026-W42", false],
        ["2026-W41", "Week 41", "/meet?week=2026-W41", false],
      ],
    );
  });

  it("viewing history keeps the real current week as 'This week'", () => {
    const opts = weekOptions({ week: "2026-W41", history_weeks: ["2026-W43", "2026-W41"] }, "2026-W41", "2026-W43");
    assert.equal(opts[0].label, "This week");
    assert.equal(opts[0].selected, false);
    assert.equal(opts.find((o) => o.week === "2026-W41")?.selected, true);
  });

  it("drops malformed history entries", () => {
    const opts = weekOptions({ week: "2026-W43", history_weeks: ["garbage", ""] }, "current");
    assert.equal(opts.length, 1);
  });
});

describe("copy and provenance", () => {
  it("deck is honest about counts and has the empty-state line", () => {
    assert.equal(meetDeck(5), "Five people whose public technical activity changed recently — ranked by evidence, not by fame.");
    assert.match(meetDeck(3), /^Three people /);
    assert.match(meetDeck(1), /^One person /);
    assert.equal(meetDeck(0), "No one stands out this week — the radar needs more evidence.");
  });

  it("labels profile affiliations self-reported unless provenance is stated", () => {
    assert.deepEqual(affiliationParts("ex-NVIDIA · Stanford"), { text: "ex-NVIDIA · Stanford", note: "self-reported" });
    assert.deepEqual(affiliationParts("Stanford (self-reported)"), { text: "Stanford (self-reported)", note: null });
    assert.deepEqual(affiliationParts("Member of robocurve (verified GitHub org)"), { text: "Member of robocurve (verified GitHub org)", note: null });
    assert.equal(affiliationParts("  "), null);
    assert.equal(affiliationParts(null), null);
  });

  it("tones", () => {
    assert.equal(priorityTone("HIGH"), "accent");
    assert.equal(priorityTone("medium"), "ink");
    assert.equal(priorityTone("WATCH"), "muted");
    assert.equal(confidenceTone("HIGH CONFIDENCE"), "ink");
    assert.equal(confidenceTone("MEDIUM CONFIDENCE"), "muted");
    assert.equal(confidenceTone("EARLY SIGNAL"), "dotted");
  });

  it("formation links prefer the slug", () => {
    assert.equal(formationHref({ slug: "robocurve", id: "x" }), "/formation/robocurve");
    assert.equal(formationHref({ slug: "", id: "abc" }), "/formation/abc");
  });

  it("is in the primary navigation", () => {
    assert.ok(NAV_PRIMARY.some((n) => n.href === "/meet"));
  });
});

describe("meeting brief action", () => {
  it("opens a cached brief, otherwise generates — or asks to unlock when read-only", () => {
    assert.deepEqual(briefAction({ brief_report_id: "r1" }, false), { kind: "open", href: "/reports/r1" });
    assert.deepEqual(briefAction({ brief_report_id: "r1" }, true), { kind: "open", href: "/reports/r1" });
    assert.deepEqual(briefAction({ brief_report_id: null }, false), { kind: "generate" });
    assert.deepEqual(briefAction({ brief_report_id: null }, true), { kind: "unlock", href: "/unlock" });
  });

  it("explains failures", () => {
    assert.match(briefErrorText(403), /unlock/i);
    assert.match(briefErrorText(null, "read_only"), /unlock/i);
    assert.match(briefErrorText(429), /rate limited/i);
    assert.equal(briefErrorText(500), "Could not generate the meeting brief");
  });
});

describe("meeting brief body", () => {
  const body = {
    name: "Ana Chen",
    date: "2026-10-20",
    context_30s: [{ text: "Builds robot control stacks.", epistemic: "FACT", source_ids: ["S1"] }],
    why_on_radar: [{ text: "New org.", epistemic: "FACT", source_ids: ["S2", "S1"] }],
    what_they_built: [],
    technical_background: [{ text: "Robotics PhD (self-reported).", epistemic: "INFERENCE", source_ids: [] }],
    what_changed: [],
    why_anti_fund: [{ text: "Fits robotics thesis.", epistemic: "MODEL_OPINION", source_ids: ["S3"] }],
    questions: ["Q1", "Q2", "Q3", "Q4", "Q5"],
    things_to_verify: ["Employment dates"],
    unknowns: [],
  };

  it("is recognised by kind or by shape", () => {
    assert.ok(isMeetingBrief("meeting_brief", {}));
    assert.ok(isMeetingBrief("unknown", body));
    assert.ok(!isMeetingBrief("brief", { what_they_do: [], why_now: [] }));
    assert.ok(!isMeetingBrief(null, null));
  });

  it("sections follow the agreed reading order", () => {
    assert.deepEqual(
      MEETING_BRIEF_SECTIONS.map((s) => s.key),
      ["context_30s", "why_on_radar", "what_they_built", "technical_background", "what_changed", "why_anti_fund", "questions", "things_to_verify", "unknowns"],
    );
    assert.equal(MEETING_BRIEF_SECTIONS.find((s) => s.key === "questions")?.numbered, true);
  });

  it("collects cited source ids and normalises list sections", () => {
    assert.deepEqual(citedSourceIds(body).sort(), ["S1", "S2", "S3"]);
    assert.deepEqual(toStrings(body.questions), ["Q1", "Q2", "Q3", "Q4", "Q5"]);
    assert.deepEqual(toStrings([{ text: "as claim" }, " ", null, 3]), ["as claim"]);
    assert.deepEqual(toStrings(undefined), []);
  });
});

describe("meet API client", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  function capture(): { url: string; method: string; body: string | null }[] {
    const calls: { url: string; method: string; body: string | null }[] = [];
    globalThis.fetch = (async (url: string | URL, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : null });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    return calls;
  }

  it("hits /api/meet, /api/meet/refresh and /api/meeting-brief with the contract bodies", async () => {
    process.env.BACKEND_URL = "http://api:8000";
    const calls = capture();
    await getMeet();
    await getMeet("2026-W42");
    await refreshMeet();
    await postMeetingBrief("p1");
    await postMeetingBrief("p1", true);
    await getReports({ entity_id: "p1", limit: 50 });
    assert.equal(calls[0].url, "http://api:8000/api/meet?week=current");
    assert.equal(calls[1].url, "http://api:8000/api/meet?week=2026-W42");
    assert.deepEqual([calls[2].url, calls[2].method], ["http://api:8000/api/meet/refresh", "POST"]);
    assert.deepEqual([calls[3].url, calls[3].method, JSON.parse(calls[3].body ?? "")], ["http://api:8000/api/meeting-brief", "POST", { person_id: "p1" }]);
    assert.deepEqual(JSON.parse(calls[4].body ?? ""), { person_id: "p1", force: true });
    assert.equal(calls[5].url, "http://api:8000/api/reports?entity_id=p1&limit=50");
  });
});
