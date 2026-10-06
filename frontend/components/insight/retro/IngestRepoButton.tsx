"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { ingestGithub } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { describeApiError } from "../apiError";

/** Ingest a not-yet-captured retrospective repository via POST /api/ingest/github. */
export function IngestRepoButton({ repo }: { repo: string }) {
  const router = useRouter();
  const toast = useToast();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, startRefresh] = useTransition();

  const run = async () => {
    setPending(true);
    setError(null);
    try {
      const out = await ingestGithub({ repo });
      if (out.run.status === "error") throw new Error(out.run.errors[0] ?? "GitHub ingest failed");
      toast(`Ingested ${repo} — ${out.run.records_accepted} records`);
      startRefresh(() => router.refresh());
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={run}
        disabled={pending}
        aria-busy={pending}
        className="border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60"
      >
        {pending ? `Fetching ${repo}… (~30s)` : `Ingest ${repo} now`}
      </button>
      {error && (
        <p role="alert" className="mt-2 font-mono text-[11px] text-accent">
          {error}
        </p>
      )}
    </div>
  );
}
