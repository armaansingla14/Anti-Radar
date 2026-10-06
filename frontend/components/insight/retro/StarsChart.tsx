"use client";

import { LineChart, type LineSeries } from "@/components/LineChart";
import { fmtCompact } from "@/lib/format";

/** LineChart with compact count formatting (client boundary for the formatter). */
export function StarsChart({
  series,
  note,
  ariaLabel,
  height = 190,
}: {
  series: LineSeries[];
  note?: string | null;
  ariaLabel?: string;
  height?: number;
}) {
  return <LineChart series={series} height={height} formatValue={(v) => fmtCompact(v)} note={note} ariaLabel={ariaLabel} />;
}
