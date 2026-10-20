/**
 * GET /api/admin/dataset?mode=demo|live[&next=/path] — choose which backend this browser reads from.
 * `demo` = the verified real-data snapshot (DEMO_BACKEND_URL); `live` clears the cookie.
 * Redirects to `next` (same-site path only) when given, otherwise returns JSON.
 */
import { NextResponse, type NextRequest } from "next/server";
import { DATASET_COOKIE } from "@/lib/session";
import { DATASET_COOKIE_MAX_AGE, cookieFlags, noStoreJson, safeNext } from "../_shared";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  const mode = request.nextUrl.searchParams.get("mode");
  if (mode !== "demo" && mode !== "live") return noStoreJson({ detail: "mode must be demo or live" }, 400);
  const next = safeNext(request.nextUrl.searchParams.get("next"));
  const out = next
    ? NextResponse.redirect(new URL(next, request.nextUrl.origin), 303)
    : NextResponse.json({ ok: true, dataset: mode }, { headers: { "Cache-Control": "no-store" } });
  out.headers.set("Cache-Control", "no-store");
  // Lax (not Strict) so following a shared /demo link from another site still lands in the demo dataset.
  if (mode === "demo") out.cookies.set(DATASET_COOKIE, "demo", { ...cookieFlags(DATASET_COOKIE_MAX_AGE), sameSite: "lax" });
  else out.cookies.set(DATASET_COOKIE, "", { ...cookieFlags(0), maxAge: 0 });
  return out;
}
