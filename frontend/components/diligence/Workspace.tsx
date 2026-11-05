/**
 * Technical diligence workspace building blocks (server components). Every statement on screen comes from
 * the API payload; empty groups are stated as gaps; nothing here recommends an investment.
 */
import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { Cite, Quiet } from "@/components/entity/primitives";
import { externalHref, fmtDate, fmtDateTime, humanize } from "@/lib/format";
import type {
  Conflict,
  DependencySection,
  DiligenceClaim,
  DiligenceDefinitions,
  DiligenceRisk,
  EvidenceRef,
  ExecutiveSummary,
  FounderQuestion,
  InfraSection,
  AiSystemsSection,
  OpenSourceHealth,
  Review,
  ReviewTargetType,
  SecuritySection,
  TestPlan,
  UnderwritingCategory,
  UnderwritingEvidence,
} from "@/lib/types-diligence";
import { ClaimStatusBadge, RatingBadge } from "./Badges";
import {
  bySeverity,
  claimHref,
  claimLabel,
  conflictLabel,
  evidenceRelation,
  executionText,
  groupEvidence,
  isFullPlan,
  ossRows,
  reviewSummary,
  rulesFor,
  showsTestPlan,
  splitReason,
  statusCounts,
  statusLabel,
  testabilityLabel,
} from "./logic";
import { ReviewActions } from "./ReviewActions";

// ── Small pieces ─────────────────────────────────────────────────────────────

/** External link that never links to a reserved/demo host; plain text otherwise. */
export function Ext({ url, demo, children, className }: { url: string | null | undefined; demo?: boolean; children: ReactNode; className?: string }) {
  const href = externalHref(url, demo);
  if (!href) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={clsx("link", className)}>
      {children}
      <span aria-hidden className="ml-0.5 text-ink-3">↗</span>
    </a>
  );
}

function SubLabel({ children, count, className }: { children: ReactNode; count?: number; className?: string }) {
  return (
    <p className={clsx("label flex items-baseline gap-2 text-ink", className)}>
      {children}
      {count !== undefined && <span className="tnum text-ink-3">{count}</span>}
    </p>
  );
}

function Bullets({ items, glyph = "—", tone = "ink", className }: { items: string[]; glyph?: string; tone?: "ink" | "muted"; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={clsx("space-y-1", className)}>
      {items.map((t, i) => (
        <li key={i} className={clsx("grid grid-cols-[16px_minmax(0,1fr)] text-[13px] leading-snug", tone === "muted" ? "text-ink-2" : "text-ink")}>
          <span aria-hidden className="text-ink-3">
            {glyph}
          </span>
          <span className="min-w-0 break-words">{t}</span>
        </li>
      ))}
    </ul>
  );
}

/** KNOWN / INFERRED / UNKNOWN — unknowns are listed, never dropped. */
export function KnownInferredUnknown({ known, inferred, unknown, className }: { known?: string[]; inferred?: string[]; unknown?: string[]; className?: string }) {
  return (
    <div className={clsx("space-y-3", className)}>
      {(known?.length ?? 0) > 0 && (
        <div>
          <SubLabel count={known!.length}>Known</SubLabel>
          <Bullets items={known!} glyph="●" className="mt-1" />
        </div>
      )}
      {(inferred?.length ?? 0) > 0 && (
        <div>
          <SubLabel count={inferred!.length}>Inferred</SubLabel>
          <Bullets items={inferred!} glyph="◆" className="mt-1" />
        </div>
      )}
      <div>
        <SubLabel count={unknown?.length ?? 0}>Unknown</SubLabel>
        {(unknown?.length ?? 0) > 0 ? (
          <Bullets items={unknown!} glyph="?" tone="muted" className="mt-1" />
        ) : (
          <p className="mt-1 text-[12.5px] text-ink-3">Nothing listed as unknown by the rules.</p>
        )}
      </div>
    </div>
  );
}

const sevCls = (s: string) =>
  ["HIGH", "CRITICAL"].includes((s ?? "").toUpperCase())
    ? "border-accent text-accent"
    : ["MEDIUM", "MODERATE"].includes((s ?? "").toUpperCase())
      ? "border-ink text-ink"
      : "border-rule-strong text-ink-2";

export function Severity({ value }: { value: string }) {
  return (
    <span className={clsx("inline-block whitespace-nowrap border px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em]", sevCls(value))}>
      {value || "—"}
    </span>
  );
}

// ── Executive summary ────────────────────────────────────────────────────────

export function ExecSummary({
  summary,
  labels,
  defs,
  narrow,
  glossaryHref = "",
}: {
  summary: ExecutiveSummary;
  labels?: Record<string, string>;
  defs?: DiligenceDefinitions;
  /** In a narrow column (report reader): 3 counts per row, stacked callouts. */
  narrow?: boolean;
  /** Page holding the glossary anchors (default: this page). */
  glossaryHref?: string;
}) {
  const rows = statusCounts(summary.counts);
  return (
    <div className="space-y-7">
      <p className="max-w-[70ch] font-serif text-[22px] leading-snug text-ink">{summary.text}</p>
      <dl className={clsx("grid grid-cols-3 gap-x-5 gap-y-4", !narrow && "md:grid-cols-6")} aria-label="Claims by status">
        {rows.map((r) => (
          <div key={r.status} className={clsx("border-t pt-2", r.status === "CONTRADICTED" && r.count > 0 ? "border-accent" : "border-ink")}>
            <dt>
              <a href={`${glossaryHref}#def-status-${r.status}`} className="label hover:text-accent" title={defs?.statuses?.[r.status]}>
                {statusLabel(r.status, labels)}
              </a>
            </dt>
            <dd className={clsx("tnum mt-1 font-serif text-[32px] leading-none", r.count === 0 ? "text-ink-3" : r.status === "CONTRADICTED" ? "text-accent" : "text-ink")}>
              {r.count}
            </dd>
          </div>
        ))}
      </dl>
      <div className={clsx("grid gap-x-8 gap-y-5", !narrow && "md:grid-cols-3")}>
        <Callout label="Strongest evidence">{summary.strongest_evidence || "—"}</Callout>
        <Callout label="Largest uncertainty">{summary.largest_uncertainty || "—"}</Callout>
        <Callout label="Critical founder question" accent>
          {summary.critical_question || "No question generated: no evidence gap met the question rules."}
        </Callout>
      </div>
    </div>
  );
}

function Callout({ label, children, accent }: { label: string; children: ReactNode; accent?: boolean }) {
  return (
    <div className={clsx("border-l-2 pl-3", accent ? "border-accent" : "border-ink")}>
      <p className={clsx("label", accent ? "text-accent" : "text-ink")}>{label}</p>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink">{children}</p>
    </div>
  );
}

// ── Evidence ─────────────────────────────────────────────────────────────────

const isCodeLike = (e: EvidenceRef) => e.publisher === "code" && /tree|file|languages|manifest/.test(e.kind);

function EvidenceItem({ e, demo, citePrefix }: { e: EvidenceRef; demo?: boolean; citePrefix: string }) {
  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-[9px] uppercase tracking-[0.08em] text-ink-3">{e.publisher_label || humanize(e.publisher)}</span>
        <span className="min-w-0 break-words text-[13px] text-ink">
          <Ext url={e.url} demo={demo}>
            {e.title || e.url}
          </Ext>
          {e.source_id && <Cite id={e.source_id} prefix={citePrefix} />}
        </span>
        <span className="font-mono text-[9px] uppercase tracking-[0.06em] text-ink-2" title="Relation to the claim · strength">
          {evidenceRelation(e)}
        </span>
      </div>
      {e.quote && (
        <blockquote
          className={clsx(
            "mt-1 border-l border-rule-strong pl-2.5 leading-snug text-ink-2 [overflow-wrap:anywhere]",
            isCodeLike(e) ? "break-all font-mono text-[11px]" : "font-serif text-[14px] italic",
          )}
        >
          {isCodeLike(e) ? e.quote : `“${e.quote}”`}
        </blockquote>
      )}
      {e.note && <p className="mt-1 text-[12px] leading-snug text-ink-2 [overflow-wrap:anywhere]">{e.note}</p>}
    </li>
  );
}

function EvidenceGroup({
  title,
  hint,
  items,
  limit,
  demo,
  citePrefix,
  empty,
  moreHref,
  tone,
}: {
  title: string;
  hint?: string;
  items: EvidenceRef[];
  limit?: number;
  demo?: boolean;
  citePrefix: string;
  empty: string;
  moreHref?: string;
  tone?: "conflict";
}) {
  const shown = limit ? items.slice(0, limit) : items;
  const rest = items.length - shown.length;
  return (
    <div className={clsx("border-t pt-2", tone === "conflict" && items.length ? "border-accent" : "border-ink")}>
      <SubLabel count={items.length} className={tone === "conflict" && items.length ? "text-accent" : undefined}>
        {title}
      </SubLabel>
      {hint && <p className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{hint}</p>}
      {items.length === 0 ? (
        <p className="mt-1.5 text-[12.5px] text-ink-3">{empty}</p>
      ) : (
        <ul className="divide-y divide-rule/70">
          {shown.map((e, i) => (
            <EvidenceItem key={`${e.artifact_key}-${i}`} e={e} demo={demo} citePrefix={citePrefix} />
          ))}
        </ul>
      )}
      {rest > 0 &&
        (moreHref ? (
          <Link href={moreHref} className="link mt-1 inline-block font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
            + {rest} more on the claim page →
          </Link>
        ) : (
          <p className="mt-1 font-mono text-[10.5px] uppercase text-ink-3">+ {rest} more</p>
        ))}
    </div>
  );
}

/** COMPANY EVIDENCE · INDEPENDENT (PUBLIC ARTIFACT / THIRD PARTY) · CONTRADICTORY · GAPS for one claim. */
export function ClaimEvidence({
  claim,
  defs,
  demo,
  limit,
  moreHref,
  citePrefix = "source",
}: {
  claim: DiligenceClaim;
  defs?: DiligenceDefinitions;
  demo?: boolean;
  limit?: number;
  moreHref?: string;
  citePrefix?: string;
}) {
  const g = groupEvidence(claim);
  const pubs = defs?.publishers ?? {};
  return (
    <div className="space-y-5">
      <EvidenceGroup
        title="Company evidence"
        hint={pubs.company}
        items={g.company}
        limit={limit}
        demo={demo}
        citePrefix={citePrefix}
        moreHref={moreHref}
        empty="No company statement recorded beyond the claim itself."
      />
      <div>
        <p className="label mb-2 text-ink-3">Independent evidence</p>
        <div className="space-y-5">
          <EvidenceGroup
            title="Public artifact"
            hint="The publisher's own code, releases and registry metadata — verifiable, but not an independent opinion."
            items={g.artifact}
            limit={limit}
            demo={demo}
            citePrefix={citePrefix}
            moreHref={moreHref}
            empty="No public code or registry evidence."
          />
          <EvidenceGroup
            title="Third party"
            hint={pubs.third_party}
            items={g.thirdParty}
            limit={limit}
            demo={demo}
            citePrefix={citePrefix}
            moreHref={moreHref}
            empty="No independent third-party source found."
          />
        </div>
      </div>
      <EvidenceGroup
        title="Contradictory evidence · evidence conflict"
        hint="Credible evidence pointing the other way: a conflict to resolve with the company, not a finding of deception."
        items={g.conflicting}
        limit={limit}
        demo={demo}
        citePrefix={citePrefix}
        moreHref={moreHref}
        tone="conflict"
        empty="None found in the collected artifacts."
      />
      {g.gaps.length > 0 && (
        <div className="border-t border-dotted border-ink-3 pt-2">
          <SubLabel>Gaps</SubLabel>
          <Bullets items={g.gaps} glyph="○" tone="muted" className="mt-1.5" />
        </div>
      )}
    </div>
  );
}

// ── Agent assessment vs human review ─────────────────────────────────────────

export function AssessmentVsReview({
  agent,
  reviews,
  targetType,
  targetId,
  writable,
  compact,
}: {
  agent: ReactNode;
  reviews: Review[] | undefined;
  targetType: ReviewTargetType;
  targetId: string | undefined;
  writable: boolean;
  compact?: boolean;
}) {
  const s = reviewSummary(reviews);
  return (
    <div className={clsx("grid grid-cols-2 border border-rule", compact ? "text-[12px]" : "text-[12.5px]")}>
      <div className="min-w-0 border-r border-rule p-2.5">
        <p className="label text-ink-3">Agent assessment</p>
        <div className="mt-1.5">{agent}</div>
        <p className="mt-1.5 text-[11px] leading-snug text-ink-3">Deterministic rules over the evidence listed.</p>
      </div>
      <div className="min-w-0 p-2.5">
        <p className="label text-ink-3">Human review</p>
        {s.reviewed ? (
          <div className="mt-1.5 space-y-1">
            <span className="inline-block border border-ink px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em] text-ink">{s.label}</span>
            {s.note && <p className="leading-snug text-ink">{s.note}</p>}
            <p className="tnum font-mono text-[10px] uppercase tracking-[0.04em] text-ink-3">
              {s.reviewer ?? "reviewer unknown"} · {s.at ? fmtDateTime(s.at) : "date unknown"}
              {s.count > 1 && ` · ${s.count} reviews`}
            </p>
          </div>
        ) : (
          <p className="mt-1.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">Awaiting human review</p>
        )}
        {targetId && <ReviewActions targetType={targetType} targetId={targetId} writable={writable} />}
      </div>
    </div>
  );
}

// ── Test plan ────────────────────────────────────────────────────────────────

function PlanRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid gap-x-4 gap-y-0.5 border-t border-rule py-2 md:grid-cols-[112px_minmax(0,1fr)]">
      <dt className="label text-ink-3">{k}</dt>
      <dd className="min-w-0 text-[13px] leading-snug text-ink">{children}</dd>
    </div>
  );
}

const list = (xs: string[] | undefined | null) => (xs && xs.length ? xs.join(" · ") : <span className="text-ink-3">Not specified by the claim.</span>);

/** The generated (never executed) test plan for one claim. */
export function TestPlanView({ plan }: { plan: TestPlan }) {
  return (
    <div>
      <p className="border border-dashed border-ink-2 px-2.5 py-1.5 font-mono text-[10.5px] uppercase leading-snug tracking-[0.04em] text-ink">
        Execution status · {executionText(plan)}
      </p>
      <dl className="mt-2">
        {plan.hypothesis && <PlanRow k="Hypothesis">{plan.hypothesis}</PlanRow>}
        <PlanRow k="Baseline">
          {plan.baseline?.name ?? <span className="text-ink-3">No baseline named in the claim.</span>}
          {plan.baseline?.version_claimed && <span className="font-mono text-[11.5px]"> · {plan.baseline.version_claimed}</span>}
          {plan.baseline?.version_guidance && <span className="mt-0.5 block text-[12px] text-ink-2">{plan.baseline.version_guidance}</span>}
        </PlanRow>
        <PlanRow k="Candidate">{plan.candidate?.description ?? "—"}</PlanRow>
        <PlanRow k="Hardware">{list(plan.hardware)}</PlanRow>
        <PlanRow k="Model / workload">
          {list([...(plan.models ?? []), ...(plan.targets ?? [])])}
          {(plan.facets?.length ?? 0) > 0 && <span className="mt-0.5 block text-[12px] text-ink-2">Facets: {plan.facets!.join(", ")}</span>}
        </PlanRow>
        <PlanRow k="Metrics">{list(plan.metrics)}</PlanRow>
        <PlanRow k="Workloads">{list(plan.workloads)}</PlanRow>
        {(plan.procedure?.length ?? 0) > 0 && (
          <PlanRow k="Procedure">
            <ol className="list-decimal space-y-0.5 pl-4">
              {plan.procedure!.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ol>
          </PlanRow>
        )}
        {(plan.pass_criteria?.length ?? 0) > 0 && (
          <PlanRow k="Pass criteria">
            <Bullets items={plan.pass_criteria!} />
          </PlanRow>
        )}
        {(plan.sandbox?.length ?? 0) > 0 && (
          <PlanRow k="Sandbox requirements">
            <Bullets items={plan.sandbox!} tone="muted" />
          </PlanRow>
        )}
      </dl>
    </div>
  );
}

// ── Claim ledger ─────────────────────────────────────────────────────────────

/** Rule, testability and the two assessments for a claim (right column of an expanded row / claim page). */
export function ClaimAssessment({
  claim,
  defs,
  slug,
  plan,
  writable,
  planOpen,
}: {
  claim: DiligenceClaim;
  defs: DiligenceDefinitions;
  slug: string;
  plan: TestPlan | null;
  writable: boolean;
  planOpen?: boolean;
}) {
  const reason = splitReason(claim.status_reason, claim.rule);
  const rules = rulesFor(defs);
  const testable = showsTestPlan(claim);
  return (
    <div className="space-y-5">
      <div className="border-t border-ink pt-2">
        <SubLabel>Status reason</SubLabel>
        <p className="mt-1.5 text-[13px] leading-snug text-ink">
          {reason.id && (
            <a href={`#def-rule-${reason.id}`} className="mr-1.5 font-mono text-[11px] text-accent hover:underline" title={rules[reason.id]}>
              {reason.id}
            </a>
          )}
          {reason.text || "—"}
        </p>
        {reason.id && rules[reason.id] && <p className="mt-1 text-[11.5px] leading-snug text-ink-3">Rule {reason.id}: {rules[reason.id]}</p>}
        <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
          Confidence {claim.confidence || "—"} · extracted by {claim.extracted_by || "—"}
        </p>
      </div>
      <div className="border-t border-ink pt-2">
        <SubLabel>Testability</SubLabel>
        <p className="mt-1.5 text-[13px] leading-snug text-ink">
          <a href={`#def-testability-${claim.testability}`} className="mr-1.5 font-mono text-[11px] text-ink hover:text-accent">
            {testabilityLabel(claim.testability)}
          </a>
          <span className="text-ink-2">{defs.testability?.[claim.testability] ?? ""}</span>
        </p>
      </div>
      <AssessmentVsReview
        agent={<ClaimStatusBadge status={claim.status} labels={defs.status_labels} definition={defs.statuses?.[claim.status]} />}
        reviews={claim.reviews}
        targetType="claim"
        targetId={claim.id}
        writable={writable}
      />
      {testable && (
        <details className="group/plan border-t border-ink pt-2" open={planOpen}>
          <summary className="label flex cursor-pointer list-none items-baseline justify-between text-ink hover:text-accent [&::-webkit-details-marker]:hidden">
            <span>Test plan · generated, not run</span>
            <span aria-hidden className="text-ink-3 group-open/plan:hidden">+</span>
            <span aria-hidden className="hidden text-ink-3 group-open/plan:inline">−</span>
          </summary>
          <div className="mt-2">
            {isFullPlan(plan) ? (
              <TestPlanView plan={plan} />
            ) : (
              <p className="text-[12.5px] text-ink-2">
                A plan exists for this claim.{" "}
                <Link href={claimHref(slug, claim.ordinal)} className="link">
                  Open the claim page to read it →
                </Link>
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

/** One ledger row: collapsed summary + expandable evidence and assessment. */
export function ClaimRow({
  claim,
  slug,
  defs,
  plan,
  writable,
  demo,
}: {
  claim: DiligenceClaim;
  slug: string;
  defs: DiligenceDefinitions;
  plan: TestPlan | null;
  writable: boolean;
  demo?: boolean;
}) {
  const review = reviewSummary(claim.reviews);
  const href = claimHref(slug, claim.ordinal);
  return (
    <li id={`claim-${claim.ordinal}`} className="scroll-mt-24 border-b border-rule">
      <details className="group/claim">
        <summary className="grid cursor-pointer list-none grid-cols-[64px_minmax(0,1fr)] gap-x-4 gap-y-2 py-4 hover:bg-panel/40 md:grid-cols-[72px_minmax(0,1fr)_180px] [&::-webkit-details-marker]:hidden">
          <span className="tnum pt-1 font-mono text-[11px] text-ink-2">
            <span aria-hidden className="mr-1 inline-block w-2 text-ink-3 group-open/claim:hidden">+</span>
            <span aria-hidden className="mr-1 hidden w-2 text-ink-3 group-open/claim:inline-block">−</span>
            {claimLabel(claim.ordinal).replace("CLAIM ", "")}
          </span>
          <div className="min-w-0">
            <p className="font-serif text-[17px] leading-snug text-ink">“{claim.text}”</p>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
              <span className="text-ink-2">{humanize(claim.claim_type)}</span>
              <span>Testability {testabilityLabel(claim.testability)}</span>
              <span>Confidence {claim.confidence}</span>
              {claim.source_url ? (
                <a href={externalHref(claim.source_url, demo) ?? undefined} target="_blank" rel="noopener noreferrer nofollow" className="link text-ink-2">
                  Source {claim.source_id ?? ""} ↗
                </a>
              ) : (
                claim.source_id && <span>Source {claim.source_id}</span>
              )}
              {claim.observed_at && <span>Seen {fmtDate(claim.observed_at)}</span>}
            </p>
          </div>
          <div className="col-start-2 flex flex-wrap items-center gap-2 md:col-start-3 md:flex-col md:items-end md:gap-1.5">
            <ClaimStatusBadge status={claim.status} labels={defs.status_labels} definition={defs.statuses?.[claim.status]} />
            <span className={clsx("font-mono text-[9.5px] uppercase tracking-[0.06em]", review.reviewed ? "text-ink" : "text-ink-3")}>
              {review.reviewed ? `Human: ${review.label}` : "Awaiting human review"}
            </span>
          </div>
        </summary>
        <div className="grid gap-x-10 gap-y-6 pb-7 pt-1 md:pl-[88px] lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <div className="min-w-0">
            <p className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px] text-ink-2">
              <span className="label text-ink-3">Claimed at</span>
              <Ext url={claim.source_url} demo={demo} className="break-all">
                {claim.source_url ?? "source unknown"}
              </Ext>
              <Link href={href} className="link ml-auto font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink">
                Claim page →
              </Link>
            </p>
            <ClaimEvidence claim={claim} defs={defs} demo={demo} limit={3} moreHref={href} />
          </div>
          <ClaimAssessment claim={claim} defs={defs} slug={slug} plan={plan} writable={writable} />
        </div>
      </details>
    </li>
  );
}

// ── Underwriting ─────────────────────────────────────────────────────────────

function dedupeEvidence(ev: UnderwritingEvidence[]): UnderwritingEvidence[] {
  const seen = new Set<string>();
  return ev.filter((e) => {
    const k = `${e.url}|${e.quote}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

export function UnderwritingCard({ u, writable, demo }: { u: UnderwritingCategory; writable: boolean; demo?: boolean }) {
  const ev = dedupeEvidence(u.evidence ?? []);
  return (
    <article id={`uw-${u.key}`} className="scroll-mt-24 border-t border-ink pt-3">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5">
        <h3 className="font-serif text-[20px] leading-tight text-ink">{u.title}</h3>
        <RatingBadge scale={u.scale} rating={u.rating} />
      </header>
      <p className="mt-2 text-[13.5px] leading-snug text-ink">{u.headline}</p>
      <KnownInferredUnknown known={u.known} inferred={u.inferred} unknown={u.unknown} className="mt-4" />
      {ev.length > 0 && (
        <div className="mt-4">
          <SubLabel count={ev.length}>Evidence</SubLabel>
          <ul className="mt-1 space-y-1">
            {ev.slice(0, 4).map((e, i) => (
              <li key={i} className="min-w-0 text-[12.5px] leading-snug">
                <Ext url={e.url} demo={demo} className="text-ink">
                  {e.title ?? e.url}
                </Ext>
                {e.source_id && <Cite id={e.source_id} />}
                {e.quote && <span className="block break-all font-mono text-[10.5px] text-ink-3">{e.quote}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="mt-4 text-[11px] leading-snug text-ink-3">
        <span className="font-mono uppercase tracking-[0.06em] text-ink-2">Rating rule ·</span> {u.rule}
      </p>
      <div className="mt-3">
        <AssessmentVsReview
          compact
          agent={<RatingBadge scale={u.scale} rating={u.rating} />}
          reviews={u.reviews}
          targetType="underwriting"
          targetId={u.target_id}
          writable={writable}
        />
      </div>
    </article>
  );
}

// ── Sections from the payload ────────────────────────────────────────────────

export function Conflicts({ items, demo, claimLink = (o) => `#claim-${o}` }: { items: Conflict[]; demo?: boolean; claimLink?: (ordinal: number) => string }) {
  if (!items.length)
    return <Quiet>No evidence conflicts found: no collected artifact points against a claim. That is a property of what was collected, not a guarantee.</Quiet>;
  return (
    <ol className="divide-y divide-rule border-y border-ink">
      {items.map((c) => (
        <li key={c.id} className="py-4">
          <p className="flex flex-wrap items-center gap-2">
            <span className="border border-accent px-1.5 font-mono text-[9.5px] uppercase leading-[16px] tracking-[0.08em] text-accent">{conflictLabel(c)}</span>
            {c.claim_ordinals.map((o) => (
              <a key={o} href={claimLink(o)} className="font-mono text-[10.5px] text-ink-2 hover:text-accent">
                {claimLabel(o)}
              </a>
            ))}
          </p>
          <p className="mt-1.5 text-[13.5px] leading-snug text-ink">{c.summary}</p>
          {c.evidence.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {c.evidence.slice(0, 4).map((e, i) => (
                <li key={i} className="text-[12.5px]">
                  <Ext url={e.url} demo={demo}>
                    {e.title || e.url}
                  </Ext>
                  {e.source_id && <Cite id={e.source_id} />}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

export function Risks({ items, demo }: { items: DiligenceRisk[]; demo?: boolean }) {
  if (!items.length) return <Quiet>No key risks met the risk rules on the collected evidence.</Quiet>;
  return (
    <ol className="divide-y divide-rule border-y border-ink">
      {bySeverity(items).map((r, i) => (
        <li key={i} className="grid grid-cols-[86px_minmax(0,1fr)] gap-x-3 py-3">
          <span className="pt-0.5">
            <Severity value={r.severity} />
          </span>
          <div className="min-w-0">
            <p className="text-[13.5px] leading-snug text-ink">
              {r.url ? (
                <Ext url={r.url} demo={demo} className="text-ink">
                  {r.title}
                </Ext>
              ) : (
                r.title
              )}
            </p>
            <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{humanize(r.category)}</p>
            {r.detail && <p className="mt-1 text-[11.5px] leading-snug text-ink-3">Rule: {r.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Unknowns({ items }: { items: string[] }) {
  if (!items.length) return <Quiet>No explicit unknowns recorded.</Quiet>;
  return (
    <ul className="grid gap-x-10 border-t border-ink pt-2 md:grid-cols-2 xl:grid-cols-3">
      {items.map((u, i) => (
        <li key={i} className="grid grid-cols-[18px_minmax(0,1fr)] border-b border-rule/70 py-1.5 text-[13px] leading-snug text-ink-2">
          <span aria-hidden className="font-mono text-ink-3">
            ?
          </span>
          <span className="min-w-0 break-words">{u}</span>
        </li>
      ))}
    </ul>
  );
}

export function Questions({ items, demo, claimLink = (o) => `#claim-${o}` }: { items: FounderQuestion[]; demo?: boolean; claimLink?: (ordinal: number) => string }) {
  if (!items.length) return <Quiet>No founder questions: no evidence gap met the question rules.</Quiet>;
  return (
    <ol className="border-t border-ink">
      {items.map((q, i) => (
        <li key={i} className="grid grid-cols-[34px_minmax(0,1fr)] gap-x-3 border-b border-rule py-4">
          <span className="tnum pt-1 font-mono text-[11px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
          <div className="min-w-0">
            <p className="font-serif text-[18px] leading-snug text-ink">{q.question}</p>
            <p className="mt-2 text-[12.5px] leading-snug text-ink-2">
              <span className="label mr-2 text-ink-3">Why this question</span>
              {q.reason}
            </p>
            <p className="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px]">
              {q.claim_ordinals.map((o) => (
                <a key={o} href={claimLink(o)} className="font-mono text-[10.5px] text-ink-2 hover:text-accent">
                  ← {claimLabel(o)}
                </a>
              ))}
              {(q.evidence ?? []).slice(0, 3).map((e, j) => (
                <span key={j} className="min-w-0">
                  <Ext url={e.url} demo={demo} className="text-ink-2">
                    {e.title ?? e.url}
                  </Ext>
                  {e.source_id && <Cite id={e.source_id} />}
                </span>
              ))}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function OssHealthBlock({ oss, demo }: { oss: OpenSourceHealth | null | undefined; demo?: boolean }) {
  if (!oss || oss.available === false) return <Quiet>Open-source health not assessed: no public repository was collected.</Quiet>;
  const rows = ossRows(oss.metrics);
  return (
    <div className="space-y-5">
      <p className="flex flex-wrap items-baseline gap-x-3 text-[13px] text-ink-2">
        <span className="label text-ink-3">Repository</span>
        <Ext url={oss.repo_url} demo={demo} className="font-mono text-[12px] text-ink">
          {oss.repo ?? oss.repo_url ?? "—"}
        </Ext>
        {oss.maturity && <span className="font-mono text-[10.5px] uppercase tracking-[0.06em]">Maturity: {oss.maturity}</span>}
      </p>
      {(oss.risks?.length ?? 0) > 0 && (
        <ul className="space-y-1.5">
          {bySeverity(oss.risks).map((r) => (
            <li key={r.id} className="flex flex-wrap items-baseline gap-2 text-[13px] text-ink">
              <Severity value={r.severity} />
              <Ext url={r.url} demo={demo} className="text-ink">
                {r.title}
              </Ext>
              {r.detail && <span className="text-[11.5px] text-ink-3">{r.detail}</span>}
            </li>
          ))}
        </ul>
      )}
      <dl className="grid grid-cols-1 gap-x-8 border-t border-ink sm:grid-cols-2">
        {rows.map((r) => (
          <div key={r.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-rule/70 py-1.5" title={r.note ?? undefined}>
            <dt className="label truncate text-ink-3">{r.label}</dt>
            <dd className={clsx("tnum text-right font-mono text-[12px]", r.known ? "text-ink" : "text-ink-3")}>
              {r.url && r.known ? (
                <Ext url={r.url} demo={demo}>
                  {r.value}
                </Ext>
              ) : (
                r.value
              )}
            </dd>
          </div>
        ))}
      </dl>
      {(oss.unknown?.length ?? 0) > 0 && (
        <div>
          <SubLabel count={oss.unknown!.length}>Unknown</SubLabel>
          <Bullets items={oss.unknown!} glyph="?" tone="muted" className="mt-1" />
        </div>
      )}
    </div>
  );
}

export function SecurityBlock({ sec, demo }: { sec: SecuritySection | null | undefined; demo?: boolean }) {
  if (!sec) return <Quiet>Security review not available for this run.</Quiet>;
  const findings = bySeverity(sec.findings);
  const c = sec.counts ?? {};
  return (
    <div className="space-y-4">
      <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
        {findings.length} finding{findings.length === 1 ? "" : "s"} · {c.HIGH ?? 0} high · {c.MEDIUM ?? 0} medium · {c.LOW ?? 0} low · {sec.material_questions ?? 0} material question
        {sec.material_questions === 1 ? "" : "s"} · {sec.posture?.published_advisories ?? 0} published advisories · SECURITY.md {sec.posture?.security_md ? "present" : "absent"}
      </p>
      {sec.posture?.scope && <p className="max-w-[80ch] text-[12px] leading-snug text-ink-3">Scope: {sec.posture.scope}</p>}
      {findings.length === 0 ? (
        <Quiet>No findings in the public artifacts reviewed.</Quiet>
      ) : (
        <ol className="border-t border-ink">
          {findings.map((f) => (
            <li key={f.id} className="grid grid-cols-[78px_minmax(0,1fr)] gap-x-3 border-b border-rule py-2.5">
              <span className="pt-0.5">
                <Severity value={f.severity} />
                {f.material_question && <span className="mt-1 block font-mono text-[9px] uppercase text-accent">Founder question</span>}
              </span>
              <div className="min-w-0">
                <p className="text-[13px] leading-snug text-ink">{f.title}</p>
                {f.evidence?.path && (
                  <p className="mt-0.5 break-all font-mono text-[10.5px] leading-snug text-ink-2">
                    <Ext url={f.evidence.url} demo={demo}>
                      {f.evidence.path}
                      {f.evidence.line ? `:${f.evidence.line}` : ""}
                    </Ext>
                    {f.evidence.quote && <span className="ml-2 text-ink-3">{f.evidence.quote}</span>}
                  </p>
                )}
                {f.why_it_matters && (
                  <p className="mt-1 text-[12px] leading-snug text-ink-2">
                    <span className="label mr-1.5 text-ink-3">Why it matters</span>
                    {f.why_it_matters}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="text-[11.5px] text-ink-3">Secrets in public files are masked by the collector; nothing was scanned or probed beyond public artifacts.</p>
    </div>
  );
}

export function DependenciesBlock({ deps, demo }: { deps: DependencySection | null | undefined; demo?: boolean }) {
  if (!deps || deps.parsed === false) return <Quiet>No dependency manifests could be parsed.</Quiet>;
  const c = deps.counts ?? {};
  const eco = Object.entries(c.by_ecosystem ?? {}).map(([k, v]) => `${k} ${v}`).join(", ");
  const risks = bySeverity(deps.risks);
  const vulns = deps.vulnerabilities ?? [];
  return (
    <div className="space-y-4">
      <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
        {c.total ?? 0} dependencies{eco ? ` (${eco})` : ""} · {c.runtime ?? 0} runtime · {c.dev ?? 0} dev · {c.pinned ?? 0} pinned · {deps.manifests?.length ?? 0} manifests ·{" "}
        {deps.osv_checked ?? 0} checked against OSV
      </p>
      {vulns.length > 0 ? (
        <ul className="space-y-1.5">
          {vulns.map((v) => (
            <li key={`${v.id}-${v.name}`} className="flex flex-wrap items-baseline gap-2 text-[13px]">
              <Severity value={v.severity_level ?? "UNKNOWN"} />
              <Ext url={v.url} demo={demo} className="font-mono text-[12px] text-ink">
                {v.id}
              </Ext>
              <span className="text-ink">
                {v.name} {v.version}
              </span>
              {v.summary && <span className="text-[12px] text-ink-2">{v.summary}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] leading-snug text-ink-2">
          {(deps.osv_checked ?? 0) === 0
            ? "Known-vulnerability status is unknown: no dependency is pinned to a version, so nothing could be checked against OSV."
            : `No known vulnerabilities among the ${deps.osv_checked} pinned package${deps.osv_checked === 1 ? "" : "s"} checked against OSV.`}
        </p>
      )}
      {risks.length > 0 && (
        <ul className="space-y-1.5">
          {risks.map((r, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-2 text-[13px] text-ink">
              <Severity value={r.severity} />
              {r.title}
              {r.detail && <span className="text-[12px] text-ink-3">{r.detail}</span>}
            </li>
          ))}
        </ul>
      )}
      {(deps.not_checked_unpinned?.length ?? 0) > 0 && (
        <p className="text-[12px] leading-snug text-ink-3">
          <span className="label mr-2 text-ink-3">Not checked (unpinned)</span>
          {deps.not_checked_unpinned!.join(", ")}
        </p>
      )}
      {(deps.external_services?.length ?? 0) > 0 && (
        <p className="text-[12px] leading-snug text-ink-2">
          <span className="label mr-2 text-ink-3">External services</span>
          {deps.external_services!.map((s) => `${s.vendor}${s.category ? ` (${humanize(s.category).toLowerCase()})` : ""}`).join(", ")}
        </p>
      )}
    </div>
  );
}

export function InfraBlock({ infra }: { infra: InfraSection | null | undefined }) {
  if (!infra || infra.available === false) return <Quiet>Infrastructure not assessed for this run.</Quiet>;
  return <KnownInferredUnknown known={infra.known} inferred={infra.inferred} unknown={infra.unknown} />;
}

export function AiSystemsBlock({ ai, demo }: { ai: AiSystemsSection | null | undefined; demo?: boolean }) {
  if (!ai || !ai.items?.length) return <Quiet>No AI-system evidence assessed for this run.</Quiet>;
  return (
    <ul className="border-t border-ink">
      {ai.items.map((it) => (
        <li key={it.aspect} className="grid min-w-0 gap-x-3 gap-y-0.5 border-b border-rule/70 py-2 sm:grid-cols-[150px_76px_minmax(0,1fr)]">
          <span className="label min-w-0 break-words text-ink-2">{humanize(it.aspect)}</span>
          <span className={clsx("font-mono text-[9.5px] uppercase tracking-[0.08em]", it.state === "known" ? "text-ink" : it.state === "inferred" ? "text-ink-2" : "text-ink-3")}>
            {it.state}
          </span>
          <span className="min-w-0 break-words text-[13px] leading-snug text-ink">
            {it.detail}
            {(it.evidence ?? [])
              .filter((e) => e.url)
              .slice(0, 2)
              .map((e, i) => (
                <span key={i} className="ml-2 text-[11.5px]">
                  <Ext url={e.url} demo={demo} className="font-mono text-ink-2">
                    {e.path ?? e.item ?? "source"}
                  </Ext>
                </span>
              ))}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Compact glossary of the `definitions` payload; anchors are the targets of status/rule/testability links. */
export function Glossary({ defs }: { defs: DiligenceDefinitions }) {
  const rules = rulesFor(defs);
  const group = (title: string, prefix: string, entries: Record<string, string> | undefined, label?: (k: string) => string) =>
    entries && Object.keys(entries).length > 0 ? (
      <div className="min-w-0">
        <SubLabel>{title}</SubLabel>
        <dl className="mt-1.5">
          {Object.entries(entries).map(([k, v]) => (
            <div key={k} id={`def-${prefix}-${k}`} className="grid scroll-mt-24 grid-cols-[132px_minmax(0,1fr)] gap-x-3 border-b border-rule/70 py-1.5 target:bg-accent-wash">
              <dt className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink">{label ? label(k) : k.replace(/_/g, " ")}</dt>
              <dd className="text-[12px] leading-snug text-ink-2">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    ) : null;
  return (
    <div className="grid gap-x-10 gap-y-8 lg:grid-cols-2">
      {group("Status rules", "rule", rules)}
      {group("Verification statuses", "status", defs.statuses, (k) => statusLabel(k, defs.status_labels))}
      {group("Testability", "testability", defs.testability)}
      {group("Who published the evidence", "publisher", defs.publishers, (k) => defs.publisher_labels?.[k] ?? k)}
      {group("Claim types", "type", defs.claim_types)}
    </div>
  );
}

