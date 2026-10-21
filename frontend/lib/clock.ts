import "server-only";

/**
 * Server-side "today", honouring TODAY_OVERRIDE (YYYY-MM-DD) like the backend's
 * radar.clock so demo snapshots and the masthead agree on the date.
 */
export function today(): Date {
  const override = process.env.TODAY_OVERRIDE;
  if (override && /^\d{4}-\d{2}-\d{2}$/.test(override)) return new Date(`${override}T00:00:00Z`);
  const day = process.env.NEXT_PUBLIC_SNAPSHOT_DAY; // verified snapshot build: the data's as-of day
  if (day && /^\d{4}-\d{2}-\d{2}$/.test(day)) return new Date(`${day}T00:00:00Z`);
  return new Date();
}
