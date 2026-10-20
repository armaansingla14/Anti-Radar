"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, useTransition } from "react";
import { describeApiError } from "@/components/insight/apiError";
import { ingestX } from "@/lib/api";
import { hrefFor } from "@/lib/format";
import type { IngestOut } from "@/lib/types";
import { isXPostUrl, type XMode, xModeCopy } from "./entityLogic";

const input =
  "w-full border-0 border-b border-rule-strong bg-transparent px-0 py-1.5 font-mono text-[13px] text-ink placeholder:text-ink-3 focus:border-ink focus:outline-none disabled:opacity-60";

/**
 * X INTELLIGENCE — honest provider state plus a PASTE X POST URL form wired to POST /api/ingest/x.
 * Pasted text is stored as user-provided and never presented as verified source text.
 */
export function XIntelligence({
  mode,
  reason,
  entity,
  readOnly = false,
  compact = false,
}: {
  mode: XMode;
  reason?: string | null;
  /** Attach the post to this entity (person page). */
  entity?: { type: "person" | "company"; id: string; name: string } | null;
  readOnly?: boolean;
  compact?: boolean;
}) {
  const router = useRouter();
  const ids = useId();
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ ok: true; out: IngestOut } | { ok: false; error: string } | null>(null);
  const [, startRefresh] = useTransition();
  const copy = xModeCopy(mode, reason);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (readOnly) return;
    if (!isXPostUrl(url)) {
      setResult({ ok: false, error: "Enter an x.com or twitter.com post URL, e.g. https://x.com/handle/status/123…" });
      return;
    }
    setPending(true);
    setResult(null);
    try {
      const out = await ingestX({
        url: url.trim(),
        pasted_text: text.trim() || null,
        ...(entity ? { entity_type: entity.type, entity_id: entity.id } : {}),
      });
      setResult({ ok: true, out });
      setUrl("");
      setText("");
      startRefresh(() => router.refresh());
    } catch (err) {
      setResult({ ok: false, error: describeApiError(err) });
    } finally {
      setPending(false);
    }
  };

  return (
    <div className={clsx("grid gap-x-10 gap-y-6", !compact && "lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]")}>
      <div className="min-w-0">
        <p className={clsx("border-l-2 pl-3 font-mono text-[11px] uppercase tracking-[0.08em]", mode === "official" ? "border-ink text-ink" : "border-ink-3 text-ink-2")}>
          {copy.headline}
        </p>
        <p className="mt-2 max-w-[60ch] text-[12.5px] leading-snug text-ink-2">{copy.body}</p>
      </div>
      <form onSubmit={submit} noValidate className="min-w-0" aria-label="Paste X post URL">
        <p className="label mb-2 text-ink">Paste X post URL</p>
        <label htmlFor={`${ids}-url`} className="sr-only">
          X post URL
        </label>
        <input
          id={`${ids}-url`}
          className={input}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://x.com/handle/status/…"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          maxLength={500}
          disabled={readOnly || pending}
        />
        <label htmlFor={`${ids}-text`} className="label mt-4 mb-1 block">
          Post text <span className="text-ink-3">· optional, user-provided</span>
        </label>
        <textarea
          id={`${ids}-text`}
          rows={2}
          className={clsx(input, "resize-y font-sans text-[13px]")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={5000}
          placeholder="Paste the post text if the public embed is unavailable"
          disabled={readOnly || pending}
        />
        <p className="mt-1 text-[11px] leading-snug text-ink-3">
          Text you paste is stored as <span className="font-mono">user_provided</span> and labelled that way everywhere — it is never shown as
          verified source text. The URL is checked against the public embed when available.
          {entity && <> The post will be attached to {entity.name}.</>}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={readOnly || pending}
            aria-busy={pending}
            className="border border-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-ink transition-colors hover:bg-ink hover:text-paper disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "Recording…" : "Add post"}
          </button>
          {readOnly && (
            <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
              Read-only — <Link href="/unlock" className="link text-ink-2">unlock</Link> to add posts
            </span>
          )}
        </div>
        <div aria-live="polite">
          {result &&
            (result.ok ? (
              <p className="mt-3 border-l-2 border-ink pl-3 text-[12.5px] text-ink">
                Recorded · run {result.out.run.status} · {result.out.run.signals_created} {result.out.run.signals_created === 1 ? "signal" : "signals"}
                {typeof result.out.info?.text_source === "string" && <span className="ml-2 font-mono text-[10px] uppercase text-ink-3">text: {String(result.out.info.text_source).replace(/_/g, " ")}</span>}
                {result.out.entity && (result.out.entity.type === "person" || result.out.entity.type === "company") && (
                  <Link href={hrefFor(result.out.entity.type, result.out.entity.slug || result.out.entity.id)} className="link ml-2">
                    {result.out.entity.name} →
                  </Link>
                )}
              </p>
            ) : (
              <p role="alert" className="mt-3 border-l-2 border-accent pl-3 text-[12.5px] text-ink">
                <span className="label mr-2 text-accent">Rejected</span>
                {result.error}
              </p>
            ))}
        </div>
      </form>
    </div>
  );
}
