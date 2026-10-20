/** Backend selection: dataset cookie, production fail-loud, no silent localhost fallback. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { isProductionRuntime, isReadOnly, parseDataset, resolveBackend } from "../lib/session.ts";

describe("resolveBackend", () => {
  it("falls back to localhost only outside production", () => {
    assert.deepEqual(resolveBackend("live", { NODE_ENV: "development" }), { ok: true, base: "http://localhost:8000" });
    assert.deepEqual(resolveBackend("live", { NODE_ENV: "production", VERCEL: "1" }), {
      ok: false,
      detail: "backend_not_configured",
    });
  });

  it("requires https in production", () => {
    const env = { NODE_ENV: "production", APP_ENV: "production", BACKEND_URL: "http://radar-live.onrender.com" };
    assert.equal(resolveBackend("live", env).ok, false);
    assert.deepEqual(resolveBackend("live", { ...env, BACKEND_URL: "https://radar-live.onrender.com/" }), {
      ok: true,
      base: "https://radar-live.onrender.com",
    });
  });

  it("routes the demo dataset to DEMO_BACKEND_URL", () => {
    const env = {
      NODE_ENV: "production",
      VERCEL: "1",
      BACKEND_URL: "https://live.example.com",
      DEMO_BACKEND_URL: "https://demo.example.com",
    };
    assert.deepEqual(resolveBackend("demo", env), { ok: true, base: "https://demo.example.com" });
    assert.deepEqual(resolveBackend("live", env), { ok: true, base: "https://live.example.com" });
    // production never silently serves live data as the demo
    assert.equal(resolveBackend("demo", { ...env, DEMO_BACKEND_URL: undefined }).ok, false);
    // development: demo falls back to the live backend
    assert.deepEqual(resolveBackend("demo", { BACKEND_URL: "http://api:8000" }), { ok: true, base: "http://api:8000" });
  });

  it("parses the dataset cookie and production runtime", () => {
    assert.equal(parseDataset("demo"), "demo");
    assert.equal(parseDataset("anything"), "live");
    assert.equal(parseDataset(undefined), "live");
    assert.equal(isProductionRuntime({ NODE_ENV: "production" }), false);
    assert.equal(isProductionRuntime({ NODE_ENV: "production", APP_ENV: "production" }), true);
    assert.equal(isReadOnly(null), true);
    assert.equal(isReadOnly({ read_only: false }), false);
  });
});
