import { parseDate } from "@/lib/format";

/** "just now", "4m ago", "3h ago", "2d ago" — relative to `now` (pass the server clock to avoid drift). */
export function relativeTime(value: string | null | undefined, now: Date): string | null {
  const d = parseDate(value);
  if (!d) return null;
  const s = Math.round((now.getTime() - d.getTime()) / 1000);
  if (s < 0) return "in the future";
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h}h ago`;
  const days = Math.round(h / 24);
  if (days < 60) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}
