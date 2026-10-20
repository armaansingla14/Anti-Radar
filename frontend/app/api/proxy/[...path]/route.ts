/**
 * Same-origin proxy from the browser to the FastAPI backend.
 *
 * - Only allow-listed top-level API sections are reachable.
 * - Only JSON/SSE-relevant headers are forwarded in either direction; cookies,
 *   auth headers and the backend address never reach the client.
 * - Responses are streamed through unbuffered so SSE (/investigate) works.
 * - Backend: BACKEND_URL, or DEMO_BACKEND_URL when the `radar_dataset=demo` cookie is set. In production a
 *   missing/non-https URL is a 503 `backend_not_configured` (never a silent localhost fallback).
 * - Writes: the httpOnly `radar_admin` cookie (set by /api/admin/unlock) is forwarded as `X-Radar-Admin`;
 *   cross-site writes are refused (Origin check, on top of the SameSite=Strict cookie).
 * - A sleeping backend (Render free tier cold start) yields 503 `backend_waking` so the UI can say so.
 */
import type { NextRequest } from "next/server";
import { ADMIN_COOKIE, ADMIN_HEADER, DATASET_COOKIE, parseDataset, resolveBackend } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Long-running SSE investigations (Fluid compute); the backend itself may also need ~60 s to wake.
export const maxDuration = 300;

/** How long to wait for response headers (a cold Render instance takes 30–60 s to start). */
const HEADERS_TIMEOUT_MS = 100_000;

const ALLOWED_SECTIONS = new Set([
  "health",
  "session",
  "radar",
  "home",
  "daily",
  "brief",
  "companies",
  "founders",
  "formation",
  "meet",
  "meeting-brief",
  "signals",
  "thesis",
  "watchlist",
  "search",
  "status",
  "retrospective",
  "graph",
  "reports",
  "feedback",
  "investigate",
  "memo",
  "ingest",
]);

const MAX_BODY_BYTES = 256 * 1024;
const FORWARD_RESPONSE_HEADERS = ["content-type", "content-disposition", "cache-control", "etag", "last-modified"];
const SEGMENT = /^[A-Za-z0-9._~@:+-]+$/;

function jsonError(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

/** Writes must come from this site (browsers always send Origin on cross-origin POST/DELETE). */
function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true; // same-origin fetches from older browsers / server-side callers
  try {
    const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

function validPath(segments: string[]): boolean {
  if (segments.length === 0 || !ALLOWED_SECTIONS.has(segments[0])) return false;
  return segments.every((s) => s !== "." && s !== ".." && SEGMENT.test(s));
}

async function proxy(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  if (!validPath(path)) return jsonError(404, "Unknown API path");

  const backend = resolveBackend(parseDataset(request.cookies.get(DATASET_COOKIE)?.value));
  if (!backend.ok) return jsonError(503, backend.detail);

  const isWrite = request.method !== "GET" && request.method !== "HEAD";
  if (isWrite && !sameOrigin(request)) return jsonError(403, "cross_site_write");

  const target = `${backend.base}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers({ Accept: request.headers.get("accept") ?? "application/json" });
  const admin = request.cookies.get(ADMIN_COOKIE)?.value;
  if (admin) headers.set(ADMIN_HEADER, admin);

  let body: ArrayBuffer | undefined;
  if (request.method === "POST") {
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (declared > MAX_BODY_BYTES) return jsonError(413, "Request body too large");
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return jsonError(413, "Request body too large");
    headers.set("Content-Type", request.headers.get("content-type") ?? "application/json");
  }

  // Abort on client disconnect, or if the backend sends no headers in time. The timer is cleared once
  // headers arrive so long SSE bodies are not cut off.
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  request.signal.addEventListener("abort", onAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), HEADERS_TIMEOUT_MS);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return jsonError(503, "backend_waking");
  } finally {
    clearTimeout(timer);
  }

  const out = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  const isStream = (upstream.headers.get("content-type") ?? "").includes("text/event-stream");
  if (isStream) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("Connection", "keep-alive");
    out.set("X-Accel-Buffering", "no");
  } else if (!out.has("cache-control")) {
    out.set("Cache-Control", "no-store");
  }
  if (upstream.status >= 300 && upstream.status < 400) return jsonError(502, "Unexpected backend redirect");
  // Render's edge answers 502/503/504 with an HTML page while an instance spins up.
  if ([502, 503, 504].includes(upstream.status) && !(upstream.headers.get("content-type") ?? "").includes("json")) {
    await upstream.body?.cancel().catch(() => undefined);
    return jsonError(503, "backend_waking");
  }

  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
