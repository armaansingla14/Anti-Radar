import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { EmptyState } from "@/components/EmptyState";
import { loadDiligence } from "@/components/entity/data";
import { Contents, Quiet, Section } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { DiligenceStateBadge } from "@/components/diligence/Badges";
import { RunDiligenceButton } from "@/components/diligence/RunButton";
import {
  AiSystemsBlock,
  ClaimRow,
  Conflicts,
  DependenciesBlock,
  ExecSummary,
  Ext,
  Glossary,
  InfraBlock,
  OssHealthBlock,
  Questions,
  Risks,
  SecurityBlock,
  UnderwritingCard,
  Unknowns,
} from "@/components/diligence/Workspace";
import { canWrite, modeLabel, pct, stateReason } from "@/components/diligence/logic";
import { dataSource } from "@/lib/dataSource";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";

export async function generateMetadata({ params }: PageProps<"/diligence/[company]">): Promise<Metadata> {
  const { company } = await params;
  const ws = await loadDiligence(decodeURIComponent(company)).catch(() => null);
  return { title: ws ? `Diligence · ${ws.company.name}` : "Diligence" };
}

function Meta({ k, children, title }: { k: string; children: ReactNode; title?: string }) {
  return (
    <div className="min-w-0 border-t border-rule pt-2" title={title}>
      <dt className="label text-ink-3">{k}</dt>
      <dd className="tnum mt-1 text-[13px] text-ink">{children}</dd>
    </div>
  );
}

export default async function DiligenceWorkspacePage({ params }: PageProps<"/diligence/[company]">) {
  const { company } = await params;
  const [ws, session] = await Promise.all([loadDiligence(decodeURIComponent(company)), getSession()]);
  if (!ws) notFound();

  const c = ws.company;
  const demo = c.is_demo;
  const writable = canWrite(session, dataSource() === "snapshot");
  const run = ws.run;
  const reason = stateReason(ws.status);
  const defs = ws.definitions ?? {};
  const s = ws.sections ?? {};

  const header = (
    <header className="border-b-[3px] border-double border-ink pb-7" data-demo="diligence-header">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Link href="/diligence" className="label link text-ink-2">
          Diligence
        </Link>
        <span aria-hidden className="h-px w-5 bg-ink-3" />
        <span className="label text-ink">Technical diligence</span>
        {demo && <DemoBadge />}
      </div>
      <div className="mt-5 grid gap-x-10 gap-y-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <h1 className="font-serif text-[52px] leading-[1.0] tracking-[-0.02em] text-ink max-md:text-[38px]">{c.name}</h1>
          <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
            {ws.header.technical_domain ?? c.technical_domain ?? "Technical domain unknown"}
            {ws.header.date && <> · {fmtDate(ws.header.date)}</>}
          </p>
          <p className="mt-4 max-w-[70ch] text-[13px] leading-relaxed text-ink-2">
            What {c.name} claims about its technology, the public evidence for and against each claim, and what remains unknown. An
            evidence assessment from documented rules — not an investment recommendation.
          </p>
        </div>
        <div className="min-w-0 self-start border-t border-ink pt-3">
          <p className="label text-ink-3">Diligence status</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <DiligenceStateBadge state={ws.status.status} large />
            <RunDiligenceButton slug={c.slug} writable={writable} label={run ? "Re-run diligence" : "Run diligence"} />
          </div>
          {reason && <p className="mt-2 text-[12.5px] leading-snug text-ink">{reason}</p>}
        </div>
      </div>
      {run && (
        <dl className="mt-7 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4 xl:grid-cols-8">
          <Meta k="Collected" title={fmtDateTime(run.collected_at)}>
            {fmtDate(run.collected_at)}
          </Meta>
          <Meta k="Evidence coverage" title="Share of material claims with evidence beyond the company's own statements">
            <span className="font-serif text-[20px] leading-none">{pct(ws.header.evidence_coverage)}</span>
          </Meta>
          <Meta k="Sources analyzed">
            <span className="font-serif text-[20px] leading-none">{ws.header.sources_analyzed}</span>
          </Meta>
          <Meta k="Last updated" title={fmtDateTime(ws.header.last_updated)}>
            {fmtDate(ws.header.last_updated)}
          </Meta>
          <Meta k="Method">
            <span className="font-mono text-[11.5px]">{ws.method_version}</span>
          </Meta>
          <Meta k="Mode" title={modeLabel(run.mode)}>
            <span className="font-mono text-[11.5px] uppercase">{run.mode}</span>
            {run.trace_summary && (
              <span className="block font-mono text-[10px] text-ink-3">
                {run.trace_summary.requests} requests · {run.trace_summary.errors} errors
              </span>
            )}
          </Meta>
          <Meta k="Company">
            <Link href={`/companies/${c.slug}`} className="link">
              Company page →
            </Link>
          </Meta>
          <Meta k="Report">
            {ws.report_id ? (
              <Link href={`/reports/${ws.report_id}`} className="link">
                Diligence report →
              </Link>
            ) : (
              <span className="text-ink-3">—</span>
            )}
          </Meta>
        </dl>
      )}
    </header>
  );

  if (!run) {
    return (
      <article className="pb-16">
        {header}
        <EmptyState className="mt-10" title="Diligence has not been run for this company.">
          {writable
            ? "Run it to collect public artifacts (website, repositories, registries, independent discussion) and analyze them offline."
            : "Running diligence needs a live, writable backend; this deployment is read-only."}
          {c.website || c.github_org ? null : " There is no website or GitHub organization on record to collect from."}
        </EmptyState>
      </article>
    );
  }

  const n = (() => {
    let i = 0;
    return () => ++i;
  })();
  const contents: { id: string; title: string }[] = [
    { id: "summary", title: "Executive summary" },
    { id: "claims", title: "Claim ledger" },
    { id: "underwriting", title: "Technical underwriting" },
    { id: "conflicts", title: "Evidence conflicts" },
    { id: "oss", title: "Open-source health" },
    { id: "security", title: "Security" },
    { id: "dependencies", title: "Dependencies" },
    { id: "systems", title: "Infrastructure & AI systems" },
    { id: "risks", title: "Key risks" },
    { id: "unknowns", title: "Unknowns" },
    { id: "questions", title: "Questions for the founders" },
    { id: "sources", title: "Sources" },
    { id: "method", title: "Method & definitions" },
  ];

  return (
    <article className="pb-16">
      {header}
      <Contents items={contents} />

      <div className="mt-12 space-y-16">
        <Section n={n()} id="summary" title="Executive summary">
          {ws.executive_summary ? (
            <ExecSummary summary={ws.executive_summary} labels={defs.status_labels} defs={defs} />
          ) : (
            <Quiet>No executive summary in this run.</Quiet>
          )}
        </Section>

        <Section
          n={n()}
          id="claims"
          title="Claim ledger"
          right={<span className="tnum">{ws.claims.length} claims<span className="max-sm:hidden"> · click a row to expand</span></span>}
        >
          {ws.claims.length === 0 ? (
            <Quiet>No material technical claims were extracted from the collected company statements.</Quiet>
          ) : (
            <ol className="border-t border-ink">
              {ws.claims.map((cl) => (
                <ClaimRow key={cl.ordinal} claim={cl} slug={c.slug} defs={defs} plan={cl.test_plan} writable={writable} demo={demo} />
              ))}
            </ol>
          )}
        </Section>

        <Section n={n()} id="underwriting" title="Technical underwriting" right={<span>no overall score</span>}>
          <p className="mb-6 max-w-[80ch] text-[12.5px] leading-relaxed text-ink-2">
            Two scales, never mixed: <span className="font-mono text-[11px] text-ink">Evidence</span> ratings (STRONG · MEDIUM · WEAK · UNKNOWN)
            say how well public evidence supports the category; <span className="font-mono text-[11px] text-ink">Risk</span> ratings (LOW ·
            MODERATE · HIGH · UNKNOWN) say how much exposure the evidence shows. Each card names its rule.
          </p>
          {ws.underwriting.length === 0 ? (
            <Quiet>No underwriting categories in this run.</Quiet>
          ) : (
            <div className="grid gap-x-12 gap-y-12 lg:grid-cols-2">
              {ws.underwriting.map((u) => (
                <UnderwritingCard key={u.key} u={u} writable={writable} demo={demo} />
              ))}
            </div>
          )}
        </Section>

        <Section n={n()} id="conflicts" title="Evidence conflicts" right={<span className="tnum">{ws.contradictions.length}</span>}>
          <Conflicts items={ws.contradictions} demo={demo} />
        </Section>

        <div className="grid gap-x-12 gap-y-16 lg:grid-cols-2">
          <Section n={n()} id="oss" title="Open-source health">
            <OssHealthBlock oss={s.open_source_health} demo={demo} />
          </Section>
          <Section n={n()} id="security" title="Security · public artifacts">
            <SecurityBlock sec={s.security} demo={demo} />
          </Section>
        </div>

        <div className="grid gap-x-12 gap-y-16 lg:grid-cols-2">
          <Section n={n()} id="dependencies" title="Dependencies">
            <DependenciesBlock deps={s.dependencies} demo={demo} />
          </Section>
          <Section n={n()} id="systems" title="Infrastructure & AI systems">
            <div className="space-y-6">
              <InfraBlock infra={s.infrastructure} />
              <div>
                <p className="label mb-2 text-ink">AI systems</p>
                <AiSystemsBlock ai={s.ai_systems} demo={demo} />
              </div>
            </div>
          </Section>
        </div>

        <Section n={n()} id="risks" title="Key risks" right={<span className="tnum">{ws.risks.length}</span>}>
          <Risks items={ws.risks} demo={demo} />
        </Section>

        <Section n={n()} id="unknowns" title="Unknowns" right={<span className="tnum">{ws.unknowns.length} explicit<span className="max-sm:hidden"> · not negative findings</span></span>}>
          <Unknowns items={ws.unknowns} />
        </Section>

        <Section n={n()} id="questions" title="Questions for the founders" right={<span className="tnum">{ws.questions.length}</span>}>
          <Questions items={ws.questions} demo={demo} />
        </Section>

        <Section n={n()} id="sources" title="Sources" right={<span className="tnum">{ws.sources.length} cited<span className="max-sm:hidden"> · {ws.header.sources_analyzed} artifacts analyzed</span></span>}>
          <Sources sources={ws.sources} demo={demo} />
        </Section>

        <Section n={n()} id="method" title="Method & definitions">
          <p className="mb-6 max-w-[84ch] text-[12.5px] leading-relaxed text-ink-2">
            Public artifacts are collected with an SSRF guard, robots.txt and size caps, and are never executed. Claims are extracted from the
            company&apos;s own statements; each status is assigned by the first matching rule below over the evidence listed, grouped by who
            published it. Test plans are generated for testable claims and are not run. Method {ws.method_version}
            {ws.report_id && (
              <>
                {" "}
                · full write-up in the{" "}
                <Link href={`/reports/${ws.report_id}`} className="link">
                  diligence report
                </Link>
              </>
            )}
            . <Ext url="https://github.com/armaansingla14/Anti-Radar/blob/main/docs/DILIGENCE_METHOD.md">docs/DILIGENCE_METHOD.md</Ext>
          </p>
          <Glossary defs={defs} />
        </Section>
      </div>
    </article>
  );
}
