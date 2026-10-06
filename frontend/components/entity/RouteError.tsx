"use client";

import Link from "next/link";
import { useEffect } from "react";

/** Shared error boundary body for entity/report routes. */
export function RouteError({ error, retry, what, back }: { error: Error & { digest?: string }; retry: () => void; what: string; back: { href: string; label: string } }) {
  useEffect(() => {
    console.warn(`[anti radar] ${what} failed to load`, error.digest ?? error.message);
  }, [error, what]);
  return (
    <div className="mx-auto max-w-[620px] py-16 text-center" role="alert">
      <p className="label mb-4 text-accent">Could not load {what}</p>
      <h1 className="font-serif text-[40px] leading-tight tracking-[-0.01em]">The radar API did not answer.</h1>
      <p className="mx-auto mt-3 max-w-[48ch] text-[15px] leading-relaxed text-ink-2">
        The backend may be restarting or rate-limiting. Nothing was lost; try again in a moment.
        {error.digest && <span className="mt-2 block font-mono text-[10.5px] text-ink-3">ref {error.digest}</span>}
      </p>
      <div className="mt-8 flex justify-center gap-6 font-mono text-[11px] uppercase tracking-[0.08em]">
        <button type="button" onClick={() => retry()} className="border border-ink px-3 py-1.5 text-ink hover:bg-ink hover:text-paper">
          Try again
        </button>
        <Link href={back.href} className="link self-center text-ink-2">
          {back.label}
        </Link>
      </div>
    </div>
  );
}
