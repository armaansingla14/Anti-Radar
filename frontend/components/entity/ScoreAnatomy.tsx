const PARTS = [
  { label: "Technical momentum", max: 25 },
  { label: "Founder signal", max: 20 },
  { label: "Acceleration", max: 20 },
  { label: "Thesis fit", max: 15 },
  { label: "Novelty", max: 10 },
  { label: "Attention gap", max: 10 },
];

/** One-line explanation of the Anti Score with its six weighted components. */
export function ScoreAnatomy() {
  return (
    <div className="min-w-0">
      <p className="max-w-[64ch] text-[14px] leading-relaxed text-ink-2">
        <span className="text-ink">Anti Score</span> (0–100) ranks how much a company&apos;s technical signals are changing before
        anyone is talking about it — six deterministic components, each explainable down to the raw inputs.
      </p>
      <div className="mt-3 flex h-[22px] w-full max-w-[640px] border-l border-ink" role="img" aria-label="Anti Score components and weights">
        {PARTS.map((p, i) => (
          <div
            key={p.label}
            className="relative border-r border-ink-3 first:bg-ink/85"
            style={{ width: `${p.max}%`, background: i === 0 ? undefined : `color-mix(in srgb, var(--color-ink) ${70 - i * 11}%, transparent)` }}
            title={`${p.label} · up to ${p.max} points`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex w-full max-w-[640px]">
        {PARTS.map((p) => (
          <span key={p.label} className="min-w-0 pr-1 font-mono text-[9px] uppercase leading-tight tracking-[0.04em] text-ink-2" style={{ width: `${p.max}%` }}>
            {p.label} <span className="tnum text-ink-3">{p.max}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
