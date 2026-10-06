/**
 * Same-origin proxy from the browser to the FastAPI backend.
 *
 * - Only allow-listed top-level API sections are reachable.
 * - Only JSON/SSE-relevant headers are forwarded in either direction; cookies,
 *   auth headers and the backend address never reach the client.
 * - Responses are streamed through unbuffered so SSE (/investigate) works.
 */
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_SECTIONS = new Set([
  "health",
  "radar",
  "daily",
  "brief",
  "companies",
  "founders",
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

function backendBase(): string {
  return (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

function jsonError(status: number, detail: string): Response {
  return Response.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

function validPath(segments: string[]): boolean {
  if (segments.length === 0 || !ALLOWED_SECTIONS.has(segments[0])) return false;
  return segments.every((s) => s !== "." && s !== ".." && SEGMENT.test(s));
}

async function proxy(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  if (!validPath(path)) return jsonError(404, "Unknown API path");

  const target = `${backendBase()}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers({ Accept: request.headers.get("accept") ?? "application/json" });

  let body: ArrayBuffer | undefined;
  if (request.method === "POST") {
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (declared > MAX_BODY_BYTES) return jsonError(413, "Request body too large");
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return jsonError(413, "Request body too large");
    headers.set("Content-Type", request.headers.get("content-type") ?? "application/json");
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    });
  } catch (err) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    const reason = err instanceof Error ? err.message : "unreachable";
    return jsonError(502, `Backend unavailable (${reason})`);
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

  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
