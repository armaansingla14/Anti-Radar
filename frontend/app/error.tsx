"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

/** Root error boundary: say what failed, offer a retry, never a blank page. */
export default function RouteError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  const [retrying, setRetrying] = useState(false);
  useEffect(() => {
    console.error(error);
  }, [error]);

  const message = error.message || "Unknown error";
  const backend = /API \d+|fetch failed|ECONNREFUSED|NetworkError|Failed to fetch/i.test(message);

  return (
    <div className="mx-auto max-w-[640px] py-16 text-center" role="alert">
      <p className="label mb-4 text-accent">Signal lost</p>
      <h1 className="font-serif text-[44px] leading-[1.05] tracking-[-0.01em] text-ink">This page could not be compiled.</h1>
      <p className="mx-auto mt-4 max-w-[48ch] text-[15px] leading-relaxed text-ink-2">
        {backend
          ? "The radar backend did not answer as expected. It may be restarting or mid-ingestion; nothing has been lost."
          : "Something went wrong while rendering this view. The data is unaffected."}
      </p>
      <p className="mx-auto mt-5 max-w-[60ch] break-words border-y border-rule py-2 font-mono text-[11px] leading-relaxed text-ink-3">
        {message}
        {error.digest && <span className="ml-2">· ref {error.digest}</span>}
      </p>
      <div className="mt-8 flex items-center justify-center gap-6 font-mono text-[11px] uppercase tracking-[0.08em]">
        <button
          type="button"
          disabled={retrying}
          onClick={() => {
            setRetrying(true);
            retry();
            window.setTimeout(() => setRetrying(false), 1500);
          }}
          className="border border-ink px-4 py-2 text-ink transition-colors duration-150 hover:bg-ink hover:text-paper disabled:opacity-60"
        >
          {retrying ? "Retrying…" : "Try again"}
        </button>
        <Link href="/status" className="link text-ink-2">
          System status
        </Link>
        <Link href="/" className="link text-ink-2">
          Radar
        </Link>
      </div>
    </div>
  );
}
