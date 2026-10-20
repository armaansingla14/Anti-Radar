/**
 * POST /api/admin/unlock {token} — verify the admin token against the live backend, then keep it in an
 * httpOnly, SameSite=Strict cookie. The proxy forwards it as X-Radar-Admin; client JS never sees it.
 */
import { NextResponse, type NextRequest } from "next/server";
import { ADMIN_COOKIE, ADMIN_HEADER, resolveBackend } from "@/lib/session";
import { ADMIN_COOKIE_MAX_AGE, cookieFlags, noStoreJson, sameOrigin } from "../_shared";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

export async function POST(request: NextRequest): Promise<Response> {
  if (!sameOrigin(request)) return noStoreJson({ detail: "cross_site_write" }, 403);
  let token = "";
  try {
    const body = (await request.json()) as { token?: unknown };
    token = typeof body.token === "string" ? body.token.trim() : "";
  } catch {
    return noStoreJson({ detail: "invalid_body" }, 400);
  }
  if (!token || token.length > 512) return noStoreJson({ detail: "invalid_token" }, 401);

  const backend = resolveBackend("live");
  if (!backend.ok) return noStoreJson({ detail: backend.detail }, 503);
  let session: { admin?: boolean; read_only?: boolean };
  try {
    const res = await fetch(`${backend.base}/api/session`, {
      cache: "no-store",
      headers: { Accept: "application/json", [ADMIN_HEADER]: token },
      signal: AbortSignal.timeout(100_000),
    });
    if (res.status >= 500) return noStoreJson({ detail: "backend_waking" }, 503);
    if (!res.ok) return noStoreJson({ detail: "backend_error" }, 502);
    session = (await res.json()) as typeof session;
  } catch {
    return noStoreJson({ detail: "backend_waking" }, 503);
  }
  if (!session.admin) return noStoreJson({ detail: "invalid_token" }, 401);

  const out = NextResponse.json({ ok: true, admin: true }, { headers: { "Cache-Control": "no-store" } });
  out.cookies.set(ADMIN_COOKIE, token, cookieFlags(ADMIN_COOKIE_MAX_AGE));
  return out;
}
