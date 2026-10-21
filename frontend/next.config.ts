import fs from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";

/**
 * DATA_SOURCE=snapshot (the public deployment): pages read the bundled verified snapshot (`snapshot/`), and
 * displayed time is pinned so it never drifts from the data: the date line shows the snapshot's as-of day, and
 * relative times ("4 h ago") are measured from the moment the snapshot was frozen (its export time).
 */
function snapshotClock(): Record<string, string> {
  if ((process.env.DATA_SOURCE ?? "").trim().toLowerCase() !== "snapshot") return {};
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(process.cwd(), "snapshot", "meta.json"), "utf8")) as { as_of?: string; exported_at?: string };
    if (!meta.as_of || !meta.exported_at) return {};
    return { NEXT_PUBLIC_SNAPSHOT_NOW: meta.exported_at, NEXT_PUBLIC_SNAPSHOT_DAY: meta.as_of };
  } catch {
    return {};
  }
}

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  // BACKEND_URL / DATA_SOURCE are read server-side only; the only public values are the pinned snapshot times.
  env: snapshotClock(),
  // The snapshot archive is read with fs at request time; make sure every server function ships it.
  outputFileTracingIncludes: { "/*": ["./snapshot/**/*"], "/**": ["./snapshot/**/*"] },
};

export default nextConfig;
