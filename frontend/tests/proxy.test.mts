/**
 * Proxy allow-list: the browser can reach exactly the sections client code fetches, writes only go
 * to write sections, and path tricks are refused.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { PROXY_ALLOWED_SECTIONS, proxyMethodAllowed, proxyPathAllowed } from "../lib/proxyPolicy.ts";

describe("proxy allow-list", () => {
  it("covers exactly the sections the browser needs", () => {
    assert.deepEqual([...PROXY_ALLOWED_SECTIONS].sort(), [
      "agents", "brief", "changes", "companies", "diligence", "feedback", "formation", "founders", "health", "home", "ingest",
      "investigate", "meet", "meeting-brief", "memo", "portfolio", "reports", "reviews", "search", "watchlist",
    ]);
  });

  it("keeps server-rendered and operational sections off the proxy", () => {
    for (const s of ["ready", "radar", "daily", "thesis", "status", "retrospective", "graph", "signals", "session", "admin", "docs", "openapi.json"]) {
      assert.equal(proxyPathAllowed([s]), false, s);
    }
  });

  it("refuses traversal and odd segments", () => {
    assert.equal(proxyPathAllowed([]), false);
    assert.equal(proxyPathAllowed(["companies", ".."]), false);
    assert.equal(proxyPathAllowed(["companies", "a/b"]), false);
    assert.equal(proxyPathAllowed(["companies", "a b"]), false);
    assert.equal(proxyPathAllowed(["companies", "halcyon-dynamics"]), true);
    assert.equal(proxyPathAllowed(["brief", "markdown"]), true);
  });

  it("only forwards writes to write sections, reads to read sections", () => {
    assert.equal(proxyMethodAllowed(["feedback"], "POST"), true);
    assert.equal(proxyMethodAllowed(["watchlist", "x"], "DELETE"), true);
    assert.equal(proxyMethodAllowed(["companies"], "POST"), false);
    assert.equal(proxyMethodAllowed(["search"], "DELETE"), false);
    assert.equal(proxyMethodAllowed(["memo"], "GET"), false);
    assert.equal(proxyMethodAllowed(["ingest", "github"], "GET"), false);
    assert.equal(proxyMethodAllowed(["companies"], "PUT"), false);
    assert.equal(proxyMethodAllowed(["reports", "abc", "markdown"], "GET"), true);
  });

  it("reviews are write-only; diligence, agents and changes are readable for the /demo/diligence pre-flight", () => {
    assert.equal(proxyMethodAllowed(["reviews"], "POST"), true);
    assert.equal(proxyMethodAllowed(["diligence", "unsloth-ai", "run"], "POST"), true);
    assert.equal(proxyMethodAllowed(["agents", "failures", "12", "review"], "POST"), true);
    assert.equal(proxyMethodAllowed(["reviews"], "GET"), false);
    assert.equal(proxyMethodAllowed(["diligence", "unsloth-ai"], "GET"), true);
    assert.equal(proxyMethodAllowed(["agents", "failures"], "GET"), true);
    assert.equal(proxyMethodAllowed(["changes"], "GET"), true);
    assert.equal(proxyMethodAllowed(["changes"], "POST"), false);
  });

  it("portfolio: reviews and question status are writes; pulse reads are open for the /demo/portfolio tour", () => {
    assert.equal(proxyMethodAllowed(["portfolio", "modal", "review"], "POST"), true);
    assert.equal(proxyMethodAllowed(["portfolio", "questions", "2dfff54b-2e6f-4095-b543-fac4c8cf844e"], "POST"), true);
    assert.equal(proxyMethodAllowed(["portfolio"], "GET"), true);
    assert.equal(proxyMethodAllowed(["portfolio", "events"], "GET"), true);
    assert.equal(proxyPathAllowed(["portfolio", "..", "admin"]), false);
  });
});
