/** POST /api/admin/lock — forget the admin token (back to read-only). */
import { NextResponse, type NextRequest } from "next/server";
import { ADMIN_COOKIE } from "@/lib/session";
import { cookieFlags, noStoreJson, sameOrigin } from "../_shared";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<Response> {
  if (!sameOrigin(request)) return noStoreJson({ detail: "cross_site_write" }, 403);
  const out = NextResponse.json({ ok: true, admin: false }, { headers: { "Cache-Control": "no-store" } });
  out.cookies.set(ADMIN_COOKIE, "", { ...cookieFlags(0), maxAge: 0 });
  return out;
}
