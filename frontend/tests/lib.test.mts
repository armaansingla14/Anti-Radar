/**
 * Unit tests for display logic that could misstate intelligence:
 * SSE parsing, query encoding, date/delta formatting, URL safety, fuzzy ranking.
 * Run with `npm test` (Node's built-in runner with native TS type stripping).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { apiUrl, parseSseChunk, toQueryString } from "../lib/api.ts";
import { dateLine, domainOf, fmtDate, fmtSigned, hrefFor, isSafeUrl, parseDate } from "../lib/format.ts";
import { fuzzyRank, fuzzyScore } from "../lib/fuzzy.ts";
import { niceDomain } from "../lib/scale.ts";

describe("parseSseChunk", () => {
  it("parses complete frames and keeps the partial remainder", () => {
    const { events, rest } = parseSseChunk('data: {"type":"step","message":"a"}\n\ndata: {"type":"done"}\n\ndata: {"ty');
    assert.deepEqual(events.map((e) => e.type), ["step", "done"]);
    assert.equal(events[0].message, "a");
    assert.equal(rest, 'data: {"ty');
  });

  it("handles CRLF, multi-line data, comments and [DONE]", () => {
    const { events } = parseSseChunk(': keepalive\r\n\r\ndata: {"type":\r\ndata: "x"}\r\n\r\ndata: [DONE]\n\n');
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "x");
  });

  it("wraps non-JSON payloads and untyped objects as messages", () => {
    const { events } = parseSseChunk('data: plain text\n\ndata: {"step":"github"}\n\n');
    assert.deepEqual(events[0], { type: "message", message: "plain text" });
    assert.equal(events[1].type, "message");
    assert.equal(events[1].step, "github");
  });
});

describe("query strings and URLs", () => {
  it("drops empty values and repeats arrays", () => {
    assert.equal(toQueryString({ q: "robot arms", empty: "", n: null, theme: ["ai", "energy"], min: 0, flag: false }), "q=robot+arms&theme=ai&theme=energy&min=0&flag=false");
  });

  it("targets BACKEND_URL on the server", () => {
    process.env.BACKEND_URL = "http://api:8000/";
    assert.equal(apiUrl("/companies/x", { limit: 5 }), "http://api:8000/api/companies/x?limit=5");
    assert.equal(apiUrl("/api/radar"), "http://api:8000/api/radar");
  });
});

describe("formatting", () => {
  it("renders the masthead date line in UTC", () => {
    assert.equal(dateLine(new Date("2026-10-06T23:30:00Z")), "TUESDAY 06 OCTOBER 2026");
  });

  it("treats date-only strings as UTC days (no off-by-one)", () => {
    assert.equal(fmtDate("2026-10-01"), "01 OCT 2026");
    assert.equal(fmtDate("2026-10-01", { year: false }), "01 OCT");
    assert.equal(fmtDate(null), "—");
    assert.equal(parseDate("not a date"), null);
  });

  it("signs deltas with a true minus and never shows -0", () => {
    assert.equal(fmtSigned(18), "+18");
    assert.equal(fmtSigned(-4.4), "−4");
    assert.equal(fmtSigned(-0.2), "0");
    assert.equal(fmtSigned(2.25, 1), "+2.3");
  });

  it("only allows http(s) links", () => {
    assert.equal(isSafeUrl("https://example.com/a"), true);
    assert.equal(isSafeUrl("javascript:alert(1)"), false);
    assert.equal(isSafeUrl("data:text/html,x"), false);
    assert.equal(isSafeUrl(null), false);
    assert.equal(domainOf("https://www.example.com/x"), "example.com");
  });

  it("routes entities to their detail pages", () => {
    assert.equal(hrefFor("company", "example-robotics"), "/companies/example-robotics");
    assert.equal(hrefFor("person", "jo sample"), "/founders/jo%20sample");
    assert.equal(hrefFor("paper", "x"), "/search?q=x");
  });
});

describe("fuzzy", () => {
  it("prefers exact, then prefix, then substring, then subsequence", () => {
    const exact = fuzzyScore("radar", "radar")!;
    const prefix = fuzzyScore("rad", "radar")!;
    const sub = fuzzyScore("dar", "radar")!;
    const seq = fuzzyScore("rdr", "radar")!;
    assert.ok(exact > prefix && prefix > sub && sub > seq);
    assert.equal(fuzzyScore("xyz", "radar"), null);
  });

  it("ranks and filters items", () => {
    const items = ["Radar", "Daily", "Founders", "Retrospective"];
    assert.deepEqual(fuzzyRank("re", items, (s) => [s]), ["Retrospective"]);
    assert.deepEqual(fuzzyRank("ds", items, (s) => [s]), ["Founders"]);
    assert.deepEqual(fuzzyRank("", items, (s) => [s]), items);
  });
});

describe("niceDomain", () => {
  it("covers the data with round ticks", () => {
    const { domain, ticks } = niceDomain(0, 758, 4);
    assert.equal(domain[0], 0);
    assert.ok(domain[1] >= 758);
    assert.deepEqual(ticks, [0, 200, 400, 600, 800]);
  });

  it("pads a flat series", () => {
    const { domain } = niceDomain(5, 5);
    assert.ok(domain[0] < 5 && domain[1] > 5);
  });
});
