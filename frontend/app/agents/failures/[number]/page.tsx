import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { FailureStatusChip, SeverityChip } from "@/components/agents/Chips";
import { FailureReviewActions } from "@/components/agents/FailureReviewActions";
import { caseHref, evidenceText, failureDisplay, failureHref, originLabel, parseFailureNumber, relatedTests, sourceRefUrl, testFileUrl } from "@/components/agents/logic";
import { canWrite } from "@/components/diligence/logic";
import { getAgentFailure, getAgentFailures, getAgents, orNull } from "@/lib/api";
import { dataSource } from "@/lib/dataSource";
import { fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";

export async function generateMetadata({ params }: PageProps<"/agents/failures/[number]">): Promise<Metadata> {
  const { number } = await params;
  const n = parseFailureNumber(number);
  return { title: n ? `${failureDisplay({ number: n })} · Agents` : "Failure · Agents" };
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid gap-x-6 gap-y-1 border-b border-rule py-3.5 md:grid-cols-[180px_minmax(0,1fr)]">
      <dt className="label pt-0.5 text-ink">{k}</dt>
      <dd className="min-w-0 text-[14px] leading-relaxed text-ink">{children}</dd>
    </div>
  );
}

function TestId({ id }: { id: string }) {
  const url = testFileUrl(id);
  return (
    <span className="block break-all font-mono text-[12px] leading-snug">
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className="link text-ink" title="Open the test file on GitHub">
          {id}
          <span aria-hidden className="ml-1 text-ink-3">↗</span>
        </a>
      ) : (
        id
      )}
    </span>
  );
}

export default async function FailurePage({ params }: PageProps<"/agents/failures/[number]">) {
  const { number } = await params;
  const n = parseFailureNumber(number);
  if (!n) notFound();
  const [f, ov, all, session] = await Promise.all([
    orNull(getAgentFailure(n)),
    getAgents().catch(() => null),
    getAgentFailures().catch(() => []),
    getSession(),
  ]);
  if (!f) notFound();
  const numbers = all.map((x) => x.number).sort((a, b) => a - b);
  const prev = [...numbers].reverse().find((x) => x < n) ?? null;
  const next = numbers.find((x) => x > n) ?? null;
  const definition = ov?.taxonomy.find((t) => t.category === f.category)?.definition ?? null;
  const related = relatedTests(f);
  const srcUrl = sourceRefUrl(f.source_ref);
  const writable = canWrite(session, dataSource() === "snapshot");

  return (
    <article className="mx-auto max-w-[980px] pb-16">
      <header className="border-b-[3px] border-double border-ink pb-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/agents" className="label link text-ink-2">
            Agents
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <Link href="/agents/failures" className="label link text-ink-2">
            Failure ledger
          </Link>
        </div>
        <p className="mt-6 tnum font-mono text-[13px] tracking-[0.08em] text-accent">{failureDisplay(f)}</p>
        <h1 className="mt-2 max-w-[40ch] font-serif text-[34px] leading-[1.12] tracking-[-0.01em] text-ink max-md:text-[26px]">{f.claim_text}</h1>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <SeverityChip value={f.severity} />
          <FailureStatusChip value={f.status} />
          <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
            {f.agent} · {f.category.replace(/_/g, " ")}
          </span>
        </div>
        <nav aria-label="Failures" className="mt-5 flex gap-5 font-mono text-[10.5px] uppercase tracking-[0.08em]">
          {prev !== null && (
            <Link href={failureHref(prev)} className="link text-ink-2">
              ← {failureDisplay({ number: prev })}
            </Link>
          )}
          <Link href="/agents/failures" className="link text-ink">
            Ledger
          </Link>
          {next !== null && (
            <Link href={failureHref(next)} className="link text-ink-2">
              {failureDisplay({ number: next })} →
            </Link>
          )}
        </nav>
      </header>

      <dl className="mt-2">
        <Row k="Agent">
          <Link href={`/agents/failures?agent=${encodeURIComponent(f.agent)}`} className="link font-mono text-[12.5px] uppercase">
            {f.agent}
          </Link>
          {f.entity_ref && <span className="ml-3 font-mono text-[11px] text-ink-3">about {f.entity_ref}</span>}
          {f.report_id && (
            <Link href={`/reports/${f.report_id}`} className="link ml-3 text-[12.5px]">
              Report →
            </Link>
          )}
        </Row>
        <Row k="Claim">
          <span className="label mb-1 block text-ink-3">What the system asserted</span>
          <span className="font-serif text-[18px] leading-snug">{f.claim_text}</span>
        </Row>
        <Row k="Evidence">
          <span className="label mb-1 block text-ink-3">Why it was wrong</span>
          {evidenceText(f.evidence_summary, f.related_tests) || <span className="text-ink-3">No evidence summary recorded.</span>}
        </Row>
        <Row k="Failure category">
          <Link href={`/agents#cat-${f.category}`} className="font-mono text-[12px] uppercase hover:text-accent">
            {f.category.replace(/_/g, " ")}
          </Link>
          {definition && <span className="mt-1 block text-[13px] text-ink-2">{definition}</span>}
        </Row>
        <Row k="Severity">
          <SeverityChip value={f.severity} />
        </Row>
        <Row k="Resolution">{f.resolution || <span className="text-ink-3">Not resolved yet.</span>}</Row>
        <Row k="Regression case">
          {f.regression_case ? (
            <Link href={caseHref(f.regression_case)} className="link font-mono text-[12px]">
              {f.regression_case}
            </Link>
          ) : (
            <span className="text-ink-3">No YAML regression case.</span>
          )}
        </Row>
        <Row k="Regression test">
          {f.regression_test ? <TestId id={f.regression_test} /> : <span className="text-ink-3">No regression test recorded{f.status === "FIXED" ? "" : " — not fixed yet"}.</span>}
        </Row>
        <Row k="Related tests">
          {related.length ? (
            <span className="space-y-1">
              {related.map((t) => (
                <TestId key={t} id={t} />
              ))}
            </span>
          ) : (
            <span className="text-ink-3">None listed.</span>
          )}
        </Row>
        <Row k="Status">
          <FailureStatusChip value={f.status} />
          <span className="ml-3 font-mono text-[10.5px] text-ink-3">
            detected {fmtDateTime(f.detected_at)} · updated {fmtDateTime(f.updated_at)}
          </span>
        </Row>
        <Row k="Origin">{originLabel(f.origin)}</Row>
        <Row k="Source">
          {f.source_ref ? (
            srcUrl ? (
              <a href={srcUrl} target="_blank" rel="noopener noreferrer" className="link">
                {f.source_ref}
                <span aria-hidden className="ml-1 text-ink-3">↗</span>
              </a>
            ) : (
              f.source_ref
            )
          ) : (
            <span className="text-ink-3">—</span>
          )}
        </Row>
        <Row k="Review history">
          {f.reviews.length === 0 ? (
            <span className="text-ink-3">No reviews recorded.</span>
          ) : (
            <ol className="space-y-2">
              {f.reviews.map((r, i) => (
                <li key={i} className="border-l border-rule-strong pl-3">
                  <FailureStatusChip value={r.decision} />
                  <span className="ml-2 font-mono text-[10.5px] text-ink-3">
                    {r.reviewer} · {fmtDateTime(r.created_at)}
                  </span>
                  {r.note && <p className="mt-1 text-[13px] text-ink">{r.note}</p>}
                </li>
              ))}
            </ol>
          )}
          <FailureReviewActions number={f.number} current={f.status} writable={writable} />
        </Row>
      </dl>
    </article>
  );
}
