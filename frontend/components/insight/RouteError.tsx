"use client";

import Link from "next/link";
import { useEffect } from "react";

export interface RouteErrorProps {
  error: Error & { digest?: string };
  retry: () => void;
  /** What the page was trying to load, e.g. "the thesis map". */
  what: string;
  /** API path that failed, shown for operators. */
  endpoint?: string;
}

/** Shared error-boundary body for insight routes: honest, quiet, recoverable. */
export function RouteError({ error, retry, what, endpoint }: RouteErrorProps) {
  useEffect(() => {
    console.warn(`[anti-radar] failed to load ${what}`, error);
  }, [error, what]);
  const unreachable = /fetch failed|ECONNREFUSED|network/i.test(error.message);
  return (
    <div className="mx-auto max-w-[680px] border-y border-ink py-12" role="alert">
      <p className="label mb-3 text-accent">Error · could not load {what}</p>
      <h1 className="font-serif text-[34px] leading-tight tracking-[-0.01em]">
        {unreachable ? "The radar backend is not answering." : "The radar returned an error."}
      </h1>
      <p className="mt-3 max-w-[56ch] text-[14px] leading-relaxed text-ink-2">
        Nothing on this page is shown rather than showing stale or partial numbers.
        {endpoint && (
          <>
            {" "}
            Endpoint: <code className="font-mono text-[12px] text-ink">{endpoint}</code>.
          </>
        )}
      </p>
      {error.message && !error.digest && (
        <pre className="mt-4 overflow-x-auto border-l border-rule-strong pl-3 font-mono text-[11px] leading-relaxed text-ink-2 whitespace-pre-wrap">
          {error.message}
        </pre>
      )}
      {error.digest && <p className="mt-4 font-mono text-[10.5px] text-ink-3">Digest {error.digest}</p>}
      <div className="mt-7 flex items-center gap-6 font-mono text-[11px] uppercase tracking-[0.08em]">
        <button
          type="button"
          onClick={() => retry()}
          className="border border-ink px-3 py-1.5 text-ink transition-colors hover:bg-ink hover:text-paper"
        >
          Try again
        </button>
        <Link href="/status" className="link text-ink-2">
          System status
        </Link>
      </div>
    </div>
  );
}
