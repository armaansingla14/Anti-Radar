/**
 * Typed API client.
 *
 * On the server (React Server Components, route handlers) requests go straight
 * to BACKEND_URL with `cache: "no-store"`. In the browser they go through the
 * same-origin proxy at /api/proxy/<path>, so the backend address never ships
 * to the client.
 */
import type {
  CompanyDetail,
  CompanySummary,
  DailyRadar,
  FeedbackRequest,
  GraphOut,
  IngestGithubRequest,
  IngestUrlRequest,
  IngestXRequest,
  InvestigateEvent,
  InvestigateRequest,
  JsonObject,
  MemoRequest,
  MorningBrief,
  PersonCard,
  PersonDetail,
  RadarFeed,
  ReportOut,
  Retrospective,
  SearchResponse,
  SignalOut,
  SystemStatus,
  ThesisMap,
  WatchlistEntry,
  WatchlistResponse,
  WatchRequest,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  readonly detail: string;

  constructor(status: number, path: string, detail: string) {
    super(`API ${status} on ${path}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.detail = detail;
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

export type QueryValue = string | number | boolean | null | undefined | (string | number)[];
export type QueryParams = Record<string, QueryValue>;

const isServer = typeof window === "undefined";

/** Base URL for a backend path ("/api/radar" → absolute or proxied URL). */
export function apiUrl(path: string, params?: QueryParams): string {
  const clean = path.startsWith("/api/") ? path.slice(4) : path.startsWith("/") ? path : `/${path}`;
  const base = isServer
    ? `${(process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "")}/api${clean}`
    : `/api/proxy${clean}`;
  const qs = toQueryString(params);
  return qs ? `${base}?${qs}` : base;
}

export function toQueryString(params?: QueryParams): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => sp.append(key, String(v)));
    else sp.append(key, String(value));
  }
  return sp.toString();
}

async function readError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail !== undefined) return JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return text.slice(0, 500) || res.statusText;
}

async function request<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  opts: { params?: QueryParams; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const res = await fetch(apiUrl(path, opts.params), {
    method,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  if (!res.ok) throw new ApiError(res.status, path, await readError(res));
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const get = <T>(path: string, params?: QueryParams) => request<T>("GET", path, { params });
const post = <T>(path: string, body: unknown) => request<T>("POST", path, { body });
const enc = encodeURIComponent;

// ── Feeds ────────────────────────────────────────────────────────────────────

export const getRadar = () => get<RadarFeed>("/radar");
export const getDaily = (params?: { since?: string }) => get<DailyRadar>("/daily", params);
export const getBrief = () => get<MorningBrief>("/brief");
export const getThesis = () => get<ThesisMap>("/thesis");
export const getStatus = () => get<SystemStatus>("/status");
export const getRetrospective = () => get<Retrospective>("/retrospective");
export const getGraph = (params?: QueryParams) => get<GraphOut>("/graph", params);

// ── Entities ─────────────────────────────────────────────────────────────────

export const getCompanies = (params?: QueryParams) => get<CompanySummary[]>("/companies", params);
export const getCompany = (slugOrId: string) => get<CompanyDetail>(`/companies/${enc(slugOrId)}`);
export const getFounders = (params?: QueryParams) => get<PersonCard[]>("/founders", params);
export const getFounder = (slugOrId: string) => get<PersonDetail>(`/founders/${enc(slugOrId)}`);
export const getSignals = (params?: QueryParams) => get<SignalOut[]>("/signals", params);
export const search = (q: string, params?: QueryParams) => get<SearchResponse>("/search", { ...params, q });

// ── Reports ──────────────────────────────────────────────────────────────────

export const getReport = (id: string) => get<ReportOut>(`/reports/${enc(id)}`);
/** Browser-usable URL for the raw markdown download of a report. */
export const reportMarkdownUrl = (id: string) => `/api/proxy/reports/${enc(id)}/markdown`;
export const postMemo = (req: MemoRequest) => post<ReportOut>("/memo", req);

// ── Workflow ─────────────────────────────────────────────────────────────────

export const getWatchlist = () => get<WatchlistResponse>("/watchlist");
export const addWatch = (req: WatchRequest) => post<WatchlistEntry>("/watchlist", req);
export const removeWatch = (itemId: string) => request<void>("DELETE", `/watchlist/${enc(itemId)}`);
export const postFeedback = (req: FeedbackRequest) => post<JsonObject>("/feedback", req);

export const ingestGithub = (req: IngestGithubRequest) => post<JsonObject>("/ingest/github", req);
export const ingestUrl = (req: IngestUrlRequest) => post<JsonObject>("/ingest/url", req);
export const ingestX = (req: IngestXRequest) => post<JsonObject>("/ingest/x", req);

// ── Investigation (SSE over POST) ────────────────────────────────────────────

/**
 * Split an SSE text buffer into complete `data:` payloads.
 * Returns parsed events and the unconsumed remainder.
 */
export function parseSseChunk(buffer: string): { events: InvestigateEvent[]; rest: string } {
  const events: InvestigateEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const rest = frames.pop() ?? "";
  for (const frame of frames) {
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data || data === "[DONE]") continue;
    try {
      const parsed = JSON.parse(data) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const obj = parsed as InvestigateEvent;
        events.push({ ...obj, type: typeof obj.type === "string" ? obj.type : "message" });
      } else {
        events.push({ type: "message", message: String(parsed) });
      }
    } catch {
      events.push({ type: "message", message: data });
    }
  }
  return { events, rest };
}

/**
 * Start an investigation and stream agent events to `onEvent`.
 * Resolves when the stream ends; rejects on HTTP error or abort.
 */
export async function investigateStream(
  req: InvestigateRequest,
  onEvent: (event: InvestigateEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(apiUrl("/investigate"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(req),
    cache: "no-store",
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, "/investigate", await readError(res));
  if (!res.body) throw new ApiError(500, "/investigate", "Empty stream");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    const { events, rest } = parseSseChunk(done ? `${buffer}\n\n` : buffer);
    buffer = rest;
    events.forEach(onEvent);
    if (done) break;
  }
}

/** Server-side helper: return null on 404 instead of throwing. */
export async function orNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.notFound) return null;
    throw err;
  }
}
