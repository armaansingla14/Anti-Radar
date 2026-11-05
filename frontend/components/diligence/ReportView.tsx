/**
 * Stored diligence report (kind `diligence`) on /reports/[id]: a compact, sourced summary with a prominent
 * link to the full workspace (where evidence, test plans and human reviews live).
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { Cite } from "@/components/entity/primitives";
import { DILIGENCE_SECTIONS } from "@/components/entity/ReportBody";
import type { DiligenceReportBody } from "@/lib/types-diligence";
import { ClaimStatusBadge, RatingBadge } from "./Badges";
import { claimHref, claimLabel, diligenceHref, splitReason, testabilityLabel } from "./logic";
import { Conflicts, ExecSummary, Questions, Risks, Unknowns } from "./Workspace";

function Sec({ k, i, children, count }: { k: string; i: number; children: ReactNode; count?: number }) {
  const def = DILIGENCE_SECTIONS.find((s) => s.key === k);
  return (
    <section id={`r-sec-${k}`} className="scroll-mt-24 border-t border-ink pb-8 pt-3">
      <h3 className="mb-4 flex items-baseline gap-3">
        <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i).padStart(2, "0")}</span>
        <span className="font-serif text-[22px] leading-tight text-ink">{def?.title ?? k}</span>
        {count !== undefined && <span className="label tnum ml-auto text-ink-3">{count}</span>}
      </h3>
      {children}
    </section>
  );
}

export function WorkspaceLink({ slug, className }: { slug: string; className?: string }) {
  return (
    <Link
      href={diligenceHref(slug)}
      className={
        className ??
        "inline-block border border-ink bg-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-paper transition-colors hover:bg-accent hover:border-accent"
      }
    >
      Open the diligence workspace →
    </Link>
  );
}

export function DiligenceReportView({ body }: { body: DiligenceReportBody }) {
  const slug = body.company?.slug;
  let i = 0;
  return (
    <div className="min-w-0">
      {body.executive_summary && (
        <Sec k="executive_summary" i={++i}>
          <ExecSummary summary={body.executive_summary} narrow glossaryHref={slug ? diligenceHref(slug) : ""} />
        </Sec>
      )}

      <Sec k="claims" i={++i} count={body.claims?.length ?? 0}>
        <ol className="divide-y divide-rule/70">
          {(body.claims ?? []).map((c) => {
            const reason = splitReason(c.status_reason, c.rule);
            return (
              <li key={c.ordinal} className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 py-3">
                <span className="tnum pt-0.5 font-mono text-[10.5px] text-ink-3">{claimLabel(c.ordinal)}</span>
                <div className="min-w-0">
                  <p className="text-[14px] leading-snug text-ink">
                    {slug ? (
                      <Link href={claimHref(slug, c.ordinal)} className="hover:text-accent">
                        “{c.text}”
                      </Link>
                    ) : (
                      <>“{c.text}”</>
                    )}
                    {c.source_id && <Cite id={c.source_id} />}
                  </p>
                  <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
                    <ClaimStatusBadge status={c.status} />
                    <span>{reason.id ? `${reason.id}: ` : ""}{reason.text}</span>
                    <span>Testability {testabilityLabel(c.testability)}</span>
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      </Sec>

      <Sec k="underwriting" i={++i} count={body.underwriting?.length ?? 0}>
        <ul className="divide-y divide-rule/70">
          {(body.underwriting ?? []).map((u) => (
            <li key={u.key} className="grid gap-x-4 gap-y-1 py-2.5 sm:grid-cols-[200px_150px_minmax(0,1fr)]">
              <span className="font-serif text-[16px] leading-tight text-ink">{u.title}</span>
              <span>
                <RatingBadge scale={u.scale} rating={u.rating} />
              </span>
              <span className="text-[13px] leading-snug text-ink-2">{u.headline}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11.5px] text-ink-3">Evidence ratings and risk ratings are separate scales; there is no overall score.</p>
      </Sec>

      <Sec k="contradictory_evidence" i={++i} count={body.contradictory_evidence?.length ?? 0}>
        <Conflicts items={body.contradictory_evidence ?? []} claimLink={slug ? (o) => claimHref(slug, o) : undefined} />
      </Sec>
      <Sec k="key_risks" i={++i} count={body.key_risks?.length ?? 0}>
        <Risks items={body.key_risks ?? []} />
      </Sec>
      <Sec k="unknowns" i={++i} count={body.unknowns?.length ?? 0}>
        <Unknowns items={body.unknowns ?? []} />
      </Sec>
      <Sec k="questions_for_founders" i={++i} count={body.questions_for_founders?.length ?? 0}>
        <Questions items={body.questions_for_founders ?? []} claimLink={slug ? (o) => claimHref(slug, o) : undefined} />
      </Sec>
    </div>
  );
}
