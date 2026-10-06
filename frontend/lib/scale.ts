/** Minimal linear scale + "nice" tick helpers for hand-rolled SVG charts. */
export function linear(domain: [number, number], range: [number, number]) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (v: number) => r0 + ((v - d0) / span) * (r1 - r0);
}

function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const norm = raw / mag;
  const step = norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1;
  return step * mag;
}

/** Extend [min,max] to round numbers and return ticks inside it. */
export function niceDomain(min: number, max: number, count = 4): { domain: [number, number]; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { domain: [0, 1], ticks: [0, 1] };
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min, count);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { domain: [lo, hi], ticks };
}
