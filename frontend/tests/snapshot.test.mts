import { strict as assert } from "node:assert";
import { describe, it } from "node:test";
import { apiUrl } from "../lib/api.ts";
import { dataSource } from "../lib/dataSource.ts";
import { resolveBackend } from "../lib/session.ts";
import { snapshotKey } from "../lib/snapshotKey.ts";
import { formationCaveat } from "../components/formation/logic.ts";

describe("verified snapshot", () => {
  it("normalises request keys: /api prefix, sorted params, empty values dropped", () => {
    assert.equal(snapshotKey(new URL("http://x/api/companies?sort=stars&limit=50&q=&offset=50")), "/companies?limit=50&offset=50&sort=stars");
    assert.equal(snapshotKey(new URL("http://x/api/formation/")), "/formation");
    assert.equal(snapshotKey(new URL("http://x/api/meet?week=current")), "/meet?week=current");
    assert.equal(snapshotKey(new URL("http://x/demo")), null);
  });

  it("server render and browser proxy produce the same key for the same request", () => {
    const server = new URL(apiUrl("/companies", { q: undefined, sort: "stars", limit: 50, offset: 0 }, "http://snapshot.invalid"));
    const proxied = new URL("http://site/api/proxy/companies?limit=50&offset=0&sort=stars");
    const proxyTarget = new URL(`http://snapshot.invalid/api/${proxied.pathname.split("/").slice(3).join("/")}${proxied.search}`);
    assert.equal(snapshotKey(server), snapshotKey(proxyTarget));
  });

  it("reads the data source from DATA_SOURCE and needs no backend URL in snapshot mode", () => {
    assert.equal(dataSource({}), "live");
    assert.equal(dataSource({ DATA_SOURCE: "Snapshot" }), "snapshot");
    assert.equal(dataSource({ DATA_SOURCE: "auto" }), "auto");
    const prod = { NODE_ENV: "production", VERCEL: "1" };
    assert.deepEqual(resolveBackend("live", prod), { ok: false, detail: "backend_not_configured" });
    assert.deepEqual(resolveBackend("live", { ...prod, DATA_SOURCE: "snapshot" }), { ok: true, base: "http://snapshot.invalid" });
  });

  it("never lets an early formation candidate read as verified", () => {
    assert.equal(formationCaveat({ kind: "person", tier: "early", families: ["github"] }), "Strong technical trajectory · formation not yet verified");
    assert.equal(formationCaveat({ kind: "team", tier: "emerging", families: ["github", "research"] }), "Converging activity · formation not yet verified");
    assert.equal(formationCaveat({ kind: "team", tier: "high", families: ["github", "research", "hiring"] }), null);
  });
});
