"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition, type FormEvent, type ReactNode } from "react";
import { ingestGithub, ingestUrl } from "@/lib/api";
import { hrefFor, humanize } from "@/lib/format";
import type { IngestOut } from "@/lib/types";
import { describeApiError } from "../apiError";
import { useStaticSnapshot } from "@/components/StaticSnapshot";

type Result = { ok: true; out: IngestOut; ms: number } | { ok: false; error: string };

const input =
  "w-full border-0 border-b border-rule-strong bg-transparent px-0 py-1.5 font-mono text-[13px] text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none";
const fieldLabel = "label mb-1 block";
const submit =
  "border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-wait disabled:opacity-60";

function entityHref(e: NonNullable<IngestOut["entity"]>): string {
  if (e.type === "company" || e.type === "person") return hrefFor(e.type, e.slug || e.id);
  return `/search?q=${encodeURIComponent(e.name)}`;
}

function infoValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function ResultView({ r }: { r: Result }) {
  if (!r.ok) {
    return (
      <div role="alert" className="mt-4 border-l-2 border-accent pl-3">
        <p className="label text-accent">Rejected</p>
        <p className="mt-1 text-[12.5px] leading-snug text-ink">{r.error}</p>
      </div>
    );
  }
  const { run, entity, info, recomputed } = r.out;
  const failed = run.status === "error" || run.status === "failed";
  return (
    <div role="status" className={clsx("mt-4 animate-rise border-l-2 pl-3", failed ? "border-accent" : "border-ink")}>
      <p className={clsx("label", failed ? "text-accent" : "text-ink")}>
        Run {humanize(run.status)} · {run.provider} · {(r.ms / 1000).toFixed(1)}s
      </p>
      <dl className="tnum mt-2 grid grid-cols-4 gap-2 font-mono text-[11px]">
        {(
          [
            ["Fetched", run.records_fetched],
            ["Accepted", run.records_accepted],
            ["Rejected", run.records_rejected],
            ["Signals", run.signals_created],
          ] as const
        ).map(([k, v]) => (
          <div key={k}>
            <dt className="text-[9.5px] uppercase tracking-[0.06em] text-ink-3">{k}</dt>
            <dd className={v ? "text-ink" : "text-ink-3"}>{v}</dd>
          </div>
        ))}
      </dl>
      {Object.keys(info ?? {}).length > 0 && (
        <dl className="mt-2 space-y-0.5 font-mono text-[10.5px]">
          {Object.entries(info).map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="text-ink-3">{k}</dt>
              <dd className="min-w-0 break-words text-ink-2">{infoValue(v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {run.errors.length > 0 && (
        <ul className="mt-2 space-y-0.5 font-mono text-[10.5px] text-accent">
          {run.errors.slice(0, 4).map((e, i) => (
            <li key={i} className="break-words">
              × {e}
            </li>
          ))}
        </ul>
      )}
      {entity ? (
        <p className="mt-2.5 text-[13px]">
          <span className="label mr-2">{humanize(entity.type)}</span>
          <Link href={entityHref(entity)} className="link text-ink">
            {entity.name} →
          </Link>
          {recomputed && <span className="ml-2 font-mono text-[10px] text-ink-3">scores recomputed</span>}
        </p>
      ) : (
        <p className="mt-2 text-[12px] text-ink-3">No entity was created or matched.</p>
      )}
    </div>
  );
}

function Panel({
  n,
  title,
  description,
  onSubmit,
  pending,
  result,
  children,
  cta,
  pendingLabel = "Running…",
}: {
  n: string;
  title: string;
  description: ReactNode;
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  pending: boolean;
  result: Result | null;
  children: ReactNode;
  cta: string;
  pendingLabel?: string;
}) {
  return (
    <form onSubmit={onSubmit} className="flex min-w-0 flex-col border-t border-ink pt-3" noValidate>
      <p className="tnum font-mono text-[11px] text-ink-3">{n}</p>
      <h3 className="mt-1 font-serif text-[21px] leading-tight text-ink">{title}</h3>
      <p className="mt-1 min-h-[38px] text-[12.5px] leading-snug text-ink-2">{description}</p>
      <div className="mt-4 space-y-4">{children}</div>
      <div className="mt-5">
        <button type="submit" disabled={pending} aria-busy={pending} className={submit}>
          {pending ? pendingLabel : cta}
        </button>
      </div>
      <div aria-live="polite">{result && <ResultView r={result} />}</div>
    </form>
  );
}

function useRun() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [, startRefresh] = useTransition();
  const run = async (fn: () => Promise<IngestOut>) => {
    setPending(true);
    setResult(null);
    const t0 = performance.now();
    try {
      const out = await fn();
      setResult({ ok: true, out, ms: performance.now() - t0 });
      startRefresh(() => router.refresh());
    } catch (err) {
      setResult({ ok: false, error: describeApiError(err) });
    } finally {
      setPending(false);
    }
  };
  return { pending, result, run, fail: (error: string) => setResult({ ok: false, error }) };
}

/** Parse "owner/name", "org" or a github.com URL. */
function parseGithub(raw: string): { repo?: string; org?: string } | null {
  let s = raw
    .trim()
    .replace(/\.git$/, "")
    .replace(/\/+$/, "");
  const m = /^(?:https?:\/\/)?(?:www\.)?github\.com\/(.+)$/i.exec(s);
  if (m) s = m[1];
  const parts = s.split("/").filter(Boolean);
  const ok = (p: string) => /^[\w.-]+$/.test(p);
  if (parts.length === 1 && ok(parts[0])) return { org: parts[0] };
  if (parts.length >= 2 && ok(parts[0]) && ok(parts[1])) return { repo: `${parts[0]}/${parts[1]}` };
  return null;
}

function GithubForm() {
  const { pending, result, run, fail } = useRun();
  const [value, setValue] = useState("");
  const parsed = value.trim() ? parseGithub(value) : null;
  return (
    <Panel
      n="A"
      title="GitHub repository or org"
      description="Fetches repo metadata, releases, contributors and star history; an org pulls its most active repositories."
      cta="Ingest from GitHub"
      pendingLabel="Fetching from GitHub — up to a minute…"
      pending={pending}
      result={result}
      onSubmit={(e) => {
        e.preventDefault();
        if (!parsed) return fail("Enter owner/name, an organisation, or a github.com URL.");
        void run(() => ingestGithub(parsed.repo ? { repo: parsed.repo } : { org: parsed.org, limit: 10 }));
      }}
    >
      <div>
        <label htmlFor="gh" className={fieldLabel}>
          owner/name · org · URL
        </label>
        <input
          id="gh"
          className={input}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="tinygrad/tinygrad"
          autoComplete="off"
          spellCheck={false}
          maxLength={200}
        />
        <p className="mt-1 h-4 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
          {value.trim()
            ? parsed
              ? parsed.repo
                ? `→ repository ${parsed.repo}`
                : `→ organisation ${parsed.org} · top 10 repos`
              : "→ not a valid GitHub reference"
            : ""}
        </p>
      </div>
    </Panel>
  );
}

function WebsiteForm() {
  const { pending, result, run, fail } = useRun();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  return (
    <Panel
      n="B"
      title="Company website"
      description="Fetches the public site (with SSRF checks) for product, team and careers pages, then matches or creates the company."
      cta="Ingest website"
      pending={pending}
      result={result}
      onSubmit={(e) => {
        e.preventDefault();
        if (!url.trim()) return fail("Enter a website URL.");
        void run(() => ingestUrl({ url: url.trim(), company_name: name.trim() || null }));
      }}
    >
      <div>
        <label htmlFor="site-url" className={fieldLabel}>
          URL
        </label>
        <input
          id="site-url"
          className={input}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example-robotics.com"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          maxLength={2000}
        />
      </div>
      <div>
        <label htmlFor="site-name" className={fieldLabel}>
          Company name <span className="text-ink-3">· optional</span>
        </label>
        <input
          id="site-name"
          className={input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Inferred from the site if blank"
          autoComplete="off"
          maxLength={200}
        />
      </div>
    </Panel>
  );
}

/** Manual ingest forms wired to POST /api/ingest/{github,url}. X posts are pasted in the X INTELLIGENCE panel. */
export function AddToRadar() {
  const staticSnapshot = useStaticSnapshot();
  if (staticSnapshot) {
    return (
      <p className="max-w-[70ch] text-[13px] leading-relaxed text-ink-2">
        This deployment serves the verified snapshot, so nothing can be added here. On a live Anti Radar backend this
        section ingests a GitHub repository, organization or company website on demand.
      </p>
    );
  }
  return (
    <div className="grid gap-x-10 gap-y-10 md:grid-cols-2">
      <GithubForm />
      <WebsiteForm />
    </div>
  );
}
