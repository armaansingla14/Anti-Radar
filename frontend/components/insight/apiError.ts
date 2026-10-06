import { ApiError } from "@/lib/api";

interface PydanticIssue {
  loc?: (string | number)[];
  msg?: string;
}

/**
 * Human-readable message from an API failure. FastAPI validation errors arrive
 * as a JSON array of issues; flatten them to "field: message".
 */
export function describeApiError(err: unknown, fallback = "The request failed."): string {
  if (err instanceof ApiError) {
    const detail = err.detail?.trim();
    if (detail?.startsWith("[")) {
      try {
        const issues = JSON.parse(detail) as PydanticIssue[];
        const lines = issues.map((i) => {
          const field = (i.loc ?? []).filter((p) => p !== "body" && p !== "query").join(".");
          const msg = (i.msg ?? "invalid value").replace(/^Value error, /, "");
          return field ? `${field}: ${msg}` : msg;
        });
        if (lines.length) return lines.join("; ");
      } catch {
        /* fall through */
      }
    }
    if (err.status >= 500 && !detail) return `Backend error (${err.status}).`;
    return detail || fallback;
  }
  if (err instanceof Error && err.name === "AbortError") return "Request cancelled.";
  if (err instanceof TypeError) return "Network error — the API proxy could not be reached.";
  return err instanceof Error && err.message ? err.message : fallback;
}
