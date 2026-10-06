/**
 * Regression tests for review findings #9 (unmeasured attention), #13 (demo
 * entities never link to real hosts), #31 (card shortcut fallbacks) and #32
 * (unknown filter values), plus data-driven person labels.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { attentionMeasured, attentionSentence, attentionShort, attentionTitle } from "../lib/attention.ts";
import { planCardAction } from "../lib/cardActions.ts";
import { resolveFilter } from "../lib/filters.ts";
import { externalHref, githubUrl, isReservedHost, NO_COMPANY_LABEL, personKindLabel, xUrl } from "../lib/format.ts";

describe("#9 attention gap with unmeasured attention", () => {
  const unmeasured = { technical: 92, attention: null, gap: 92, coverage: "low" as const };
  it("never reads as the 0th percentile", () => {
    assert.equal(attentionMeasured(unmeasured), false);
    assert.equal(attentionShort(unmeasured), "not measured");
    assert.doesNotMatch(attentionSentence(unmeasured), /0th|p0\b/);
    assert.match(attentionSentence(unmeasured), /not measured/);
    assert.equal(attentionTitle(unmeasured), "Technical p92 vs attention not measured (low coverage)");
  });
  it("still reports a measured zero as p0", () => {
    const zero = { technical: 60, attention: 0, gap: 60, coverage: "good" as const };
    assert.equal(attentionMeasured(zero), true);
    assert.equal(attentionShort(zero), "p0");
    assert.match(attentionSentence(zero), /public attention at the 0th/);
  });
});

describe("#13 demo entities never link to real hosts", () => {
  it("drops github.com / x.com profile links for demo entities", () => {
    assert.equal(githubUrl("demo-halcyon", true), null);
    assert.equal(xUrl("halcyondyn_demo", true), null);
    assert.equal(githubUrl("pytorch", false), "https://github.com/pytorch");
    assert.equal(xUrl("@karpathy", false), "https://x.com/karpathy");
  });
  it("rejects malformed handles", () => {
    assert.equal(githubUrl("../evil", false), null);
    assert.equal(xUrl("a/b", false), null);
  });
  it("allows only reserved example hosts for demo URLs", () => {
    assert.equal(externalHref("https://github.com/demo-halcyon/tactile-vla", true), null);
    assert.equal(externalHref("https://github.example/demo-halcyon", true), "https://github.example/demo-halcyon");
    assert.equal(externalHref("https://halcyon.example.com/", true), "https://halcyon.example.com/");
    assert.equal(externalHref("https://github.com/real/repo", false), "https://github.com/real/repo");
    assert.equal(externalHref("javascript:alert(1)", false), null);
    assert.equal(isReservedHost("https://notexample.com"), false);
  });
});

describe("person labels are data-driven", () => {
  it("only calls founder-linked people Founder", () => {
    assert.equal(personKindLabel({ is_founder: true }), "Founder");
    assert.equal(personKindLabel({ is_founder: false }), "Person to watch");
    assert.equal(NO_COMPANY_LABEL, "No company on record");
  });
});

describe("#31 card shortcut fallbacks", () => {
  const row = { href: "/companies/halcyon-dynamics", entityType: "company", entityId: "c1", watched: "false" };
  it("watch toggles from the card's recorded state", () => {
    assert.deepEqual(planCardAction("watch", row), { kind: "watch", entityType: "company", entityId: "c1", next: true });
    assert.deepEqual(planCardAction("watch", { ...row, watched: "true" }), { kind: "watch", entityType: "company", entityId: "c1", next: false });
    assert.equal((planCardAction("watch", { ...row, watched: undefined }) as { next: boolean }).next, true);
  });
  it("why and investigate resolve the entity from the card", () => {
    assert.deepEqual(planCardAction("why", row), { kind: "why", entityType: "company", idOrSlug: "c1" });
    assert.deepEqual(planCardAction("why", { href: "/founders/mira-okafor" }), { kind: "why", entityType: "person", idOrSlug: "mira-okafor" });
    assert.deepEqual(planCardAction("investigate", row), { kind: "navigate", href: "/companies/halcyon-dynamics?investigate=1" });
  });
  it("says so instead of silently doing nothing", () => {
    assert.equal(planCardAction("watch", { href: "/search?q=x" }).kind, "unavailable");
    assert.equal(planCardAction("investigate", { href: "/search?q=x" }).kind, "unavailable");
    assert.equal(planCardAction("why", {}).kind, "unavailable");
  });
});

describe("#32 unknown filter values", () => {
  it("drops unknown themes instead of sending them to the API", () => {
    assert.deepEqual(resolveFilter("zzz", ["robotics", "energy"]), { value: undefined, unknown: "zzz" });
    assert.deepEqual(resolveFilter("robotics", ["robotics"]), { value: "robotics", unknown: null });
    assert.deepEqual(resolveFilter("", ["robotics"]), { value: undefined, unknown: null });
    assert.deepEqual(resolveFilter("zzz", []), { value: "zzz", unknown: null });
  });
});
