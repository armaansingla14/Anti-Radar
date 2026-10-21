import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { today } from "@/lib/clock";
import { dataStatus } from "@/lib/snapshot-server";
import { dateLine } from "@/lib/format";
import { NavLinks } from "./NavLinks";

async function DateLine() {
  await connection();
  const d = today();
  return (
    <time dateTime={d.toISOString().slice(0, 10)} className="tnum font-mono text-[10.5px] tracking-[0.12em] text-ink-2">
      {dateLine(d)}
    </time>
  );
}

/** Where the data on screen comes from: the verified snapshot (quietly), or a notice when live data is unavailable. */
async function SourceLine() {
  await connection();
  const s = dataStatus();
  if (!s.fromSnapshot) return null;
  const collected = s.asOf ? dateLine(new Date(`${s.asOf}T00:00:00Z`)) : null;
  return (
    <span className="label text-ink-3" title={s.capturedAt ? `Collected ${s.capturedAt}` : undefined}>
      {s.liveUnavailable ? "Live intelligence unavailable · showing verified snapshot" : "Verified snapshot"}
      {s.liveUnavailable && collected ? ` · collected ${collected}` : ""}
    </span>
  );
}

/** Site masthead: wordmark, motto, date line and navigation, closed by a double rule. */
export function Masthead() {
  return (
    <header className="mx-auto w-full max-w-[1240px] px-6 pt-6 md:px-10">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
          <Link href="/" className="font-serif text-[30px] font-medium leading-none tracking-[0.06em] text-ink" aria-label="Anti Radar — home">
            ANTI <span className="text-accent">RADAR</span>
          </Link>
          <span className="font-serif text-[16px] italic text-ink-2">Conviction Before Consensus.</span>
        </div>
        <div className="flex items-baseline gap-4">
          <span className="label hidden text-ink-3 md:inline">Built for Anti Fund</span>
          <Suspense fallback={null}>
            <SourceLine />
          </Suspense>
          <Suspense fallback={<span className="font-mono text-[10.5px] text-ink-3">—</span>}>
            <DateLine />
          </Suspense>
        </div>
      </div>
      <div className="mt-4 border-t border-ink" />
      <NavLinks />
      <div className="border-t border-ink" />
      <div className="mt-[2px] border-t border-rule" />
    </header>
  );
}
