/** Shared helpers for the /api/admin/* route handlers (cookie flags, origin check). */
import type { NextRequest } from "next/server";

export const ADMIN_COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days
export const DATASET_COOKIE_MAX_AGE = 60 * 60 * 24 * 7; // 7 days

export function cookieFlags(maxAge: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict" as const,
    path: "/",
    maxAge,
  };
}

export function noStoreJson(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

/** Refuse cross-site state changes (SameSite=Strict covers the cookie; this covers the request itself). */
export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === (request.headers.get("x-forwarded-host") ?? request.headers.get("host"));
  } catch {
    return false;
  }
}

/** Only same-site relative paths are valid redirect targets. */
export function safeNext(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null;
  return value;
}
