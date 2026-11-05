/** Agent evaluation control plane: null metrics keep their reason, failure numbering, GitHub test links, related tests. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  caseResult,
  evidenceText,
  failureDisplay,
  failureHref,
  filterHref,
  formatMetric,
  metricFootnotes,
  parseFailureFilters,
  parseFailureNumber,
  relatedTests,
  sourceRefUrl,
  testFileUrl,
} from "../components/agents/logic.ts";

describe("metric formatting", () => {
  it("formats values by kind", () => {
    assert.equal(formatMetric({ value: 1, n: 6, reason: null }, "rate").text, "100%");
    assert.equal(formatMetric({ value: 0.7528, n: 6, reason: null }, "rate").text, "75.3%");
    assert.equal(formatMetric({ value: 55.3, n: 6, reason: null }, "ms").text, "55 ms");
    assert.equal(formatMetric({ value: 2400, n: 6, reason: null }, "ms").text, "2.4 s");
    assert.equal(formatMetric({ value: 0, n: 6, reason: null }, "usd").text, "$0");
    assert.equal(formatMetric({ value: 0.0031, n: 6, reason: null }, "usd").text, "$0.0031");
    assert.equal(formatMetric({ value: 7.5, n: 6, reason: null }, "number").text, "7.5");
    assert.equal(formatMetric({ value: 0.5, n: 4, reason: null }, "rate").title, "n = 4");
  });

  it("renders null as — with the reason, never as 0", () => {
    const f = formatMetric({ value: null, n: 0, reason: "no evaluated runs (run `radar eval`)" }, "rate");
    assert.equal(f.text, "—");
    assert.equal(f.missing, true);
    assert.equal(f.reason, "no evaluated runs (run `radar eval`)");
    assert.match(f.title, /^Not computed: /);
    assert.equal(formatMetric(undefined, "ms").text, "—");
  });

  it("numbers distinct null reasons as footnotes", () => {
    const notes = metricFootnotes([
      { metrics: { success_rate: { value: 1, n: 3, reason: null } } },
      { metrics: { success_rate: { value: null, n: 0, reason: "a" }, error_rate: { value: null, n: 0, reason: "a" } } },
      { metrics: { success_rate: { value: null, n: 0, reason: "b" } } },
    ], [
      { key: "success_rate", label: "Success", short: "Success", kind: "rate" },
      { key: "error_rate", label: "Errors", short: "Errors", kind: "rate" },
    ]);
    assert.equal(notes.get("not reported by the evaluator"), 1);
    assert.equal(notes.get("a"), 2);
    assert.equal(notes.get("b"), 3); // an absent metric is missing too, never 0
  });
});

describe("failures", () => {
  it("displays and parses failure numbers", () => {
    assert.equal(failureDisplay({ number: 12 }), "FAILURE 0012");
    assert.equal(failureDisplay({ number: 12, display: "FAILURE 0012" }), "FAILURE 0012");
    assert.equal(parseFailureNumber("0012"), 12);
    assert.equal(parseFailureNumber("0"), null);
    assert.equal(parseFailureNumber("../x"), null);
    assert.equal(failureHref(7), "/agents/failures/0007");
  });

  it("links a pytest node id to its file on GitHub", () => {
    assert.equal(
      testFileUrl("tests/unit/test_llm_guard.py::test_reported_scores_are_not_injection"),
      "https://github.com/armaansingla14/Anti-Radar/blob/main/backend/tests/unit/test_llm_guard.py",
    );
    assert.equal(
      testFileUrl("tests/evals/test_regression_cases.py::test_regression_case[why-anti-copy-001]"),
      "https://github.com/armaansingla14/Anti-Radar/blob/main/backend/tests/evals/test_regression_cases.py",
    );
    assert.equal(testFileUrl(null), null);
    assert.equal(testFileUrl("../etc/passwd.py::x"), null);
  });

  it("links documented sources and commits", () => {
    assert.equal(sourceRefUrl("docs/REAL_WORLD_EVALUATION.md §6.1"), "https://github.com/armaansingla14/Anti-Radar/blob/main/docs/REAL_WORLD_EVALUATION.md");
    assert.equal(sourceRefUrl("commit 125f81a (AI coding-agent accounts)"), "https://github.com/armaansingla14/Anti-Radar/commit/125f81a");
    assert.equal(sourceRefUrl("Hand audit of the first live diligence run"), null);
  });

  it("uses related_tests from the API and strips the duplicated sentence from the evidence", () => {
    const f = {
      evidence_summary: "Ignored the bios. Related tests: tests/a.py::test_x, tests/b.py::test_y[p].",
      regression_test: "tests/a.py::test_x",
      related_tests: ["tests/a.py::test_x", "tests/b.py::test_y[p]"],
    };
    assert.deepEqual(relatedTests(f), ["tests/b.py::test_y[p]"]);
    assert.equal(evidenceText(f.evidence_summary, f.related_tests), "Ignored the bios.");
    // Without related_tests the summary is shown whole (nothing parsed out of prose).
    assert.equal(evidenceText(f.evidence_summary, []), f.evidence_summary);
    assert.deepEqual(relatedTests({ regression_test: null, related_tests: [] }), []);
    assert.deepEqual(relatedTests({ regression_test: null }), []);
  });

  it("builds filter links that keep the other filters", () => {
    const f = parseFailureFilters({ status: "fixed", agent: "DILIGENCE ANALYST", category: "<script>" });
    assert.deepEqual(f, { status: "FIXED", category: null, agent: "DILIGENCE ANALYST" });
    assert.equal(filterHref(f, "category", "TOOL_FAILURE"), "/agents/failures?status=FIXED&category=TOOL_FAILURE&agent=DILIGENCE+ANALYST");
    assert.equal(filterHref({ status: null, category: null, agent: null }, "status", null), "/agents/failures");
  });
});

describe("regression cases", () => {
  it("reports pass, fail and never run", () => {
    assert.equal(caseResult({ latest: null }), "NOT RUN");
    assert.equal(caseResult({ latest: { passed: true, provider: "t", model: "t", run_at: null, failures: [], error: null, metrics: {} } }), "PASS");
    assert.equal(caseResult({ latest: { passed: true, provider: "t", model: "t", run_at: null, failures: [], error: "boom", metrics: {} } }), "FAIL");
  });
});
