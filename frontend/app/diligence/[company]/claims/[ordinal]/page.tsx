import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";
import { DemoBadge } from "@/components/Badges";
import { Section } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { loadDiligence } from "@/components/entity/data";
import { ClaimStatusBadge } from "@/components/diligence/Badges";
import { ClaimAssessment, ClaimEvidence, Ext, Glossary } from "@/components/diligence/Workspace";
import { canWrite, claimHref, claimLabel, diligenceHref, parseOrdinal, testabilityLabel } from "@/components/diligence/logic";
import { getDiligenceClaim, orNull } from "@/lib/api";
import { dataSource } from "@/lib/dataSource";
import { fmtDate, fmtDateTime, humanize } from "@/lib/format";
import { getSession } from "@/lib/session";

const loadClaim = cache((company: string, ordinal: number) => orNull(getDiligenceClaim(company, ordinal)));

export async function generateMetadata({ params }: PageProps<"/diligence/[company]/claims/[ordinal]">): Promise<Metadata> {
  const { company, ordinal } = await params;
  const n = parseOrdinal(ordinal);
  const ws = await loadDiligence(decodeURIComponent(company)).catch(() => null);
  return { title: n && ws ? `${claimLabel(n)} · ${ws.company.name} · Diligence` : "Claim · Diligence" };
}

export default async function ClaimPage({ params }: PageProps<"/diligence/[company]/claims/[ordinal]">) {
  const { company, ordinal } = await params;
  const n = parseOrdinal(ordinal);
  if (!n) notFound();
  const slug = decodeURIComponent(company);
  const [ws, claim, session] = await Promise.all([loadDiligence(slug), loadClaim(slug, n), getSession()]);
  if (!ws || !claim) notFound();

  const c = ws.company;
  const demo = c.is_demo;
  const defs = ws.definitions ?? {};
  const writable = canWrite(session, dataSource() === "snapshot");
  const sources = claim.sources?.length ? claim.sources : ws.sources;
  const total = ws.claims.length;

  return (
    <article className="pb-16">
      <header className="border-b-[3px] border-double border-ink pb-7">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/diligence" className="label link text-ink-2">
            Diligence
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <Link href={diligenceHref(c.slug)} className="label link text-ink-2">
            {c.name}
          </Link>
          <span aria-hidden className="h-px w-5 bg-ink-3" />
          <span className="label text-ink">{claimLabel(claim.ordinal)} of {total}</span>
          {demo && <DemoBadge />}
        </div>
        <blockquote className="mt-6 max-w-[48ch] font-serif text-[34px] leading-[1.15] tracking-[-0.01em] text-ink max-md:text-[26px]">
          “{claim.text}”
        </blockquote>
        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
          <ClaimStatusBadge status={claim.status} labels={defs.status_labels} definition={defs.statuses?.[claim.status]} className="px-2 py-[3px] text-[10.5px]" />
          <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
            {humanize(claim.claim_type)} · Testability {testabilityLabel(claim.testability)} · Confidence {claim.confidence}
          </span>
        </div>
        {claim.source_quote && claim.source_quote !== claim.text && (
          <p className="mt-4 max-w-[80ch] border-l border-rule-strong pl-3 font-serif text-[15px] italic leading-snug text-ink-2">“{claim.source_quote}”</p>
        )}
        <p className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px] text-ink-2">
          <span className="label text-ink-3">Claimed at</span>
          <Ext url={claim.source_url} demo={demo} className="break-all">
            {claim.source_url ?? "source unknown"}
          </Ext>
          {claim.observed_at && <span className="font-mono text-[10.5px] uppercase text-ink-3" title={fmtDateTime(claim.observed_at)}>Observed {fmtDate(claim.observed_at)}</span>}
        </p>
        <nav aria-label="Claims" className="mt-5 flex flex-wrap gap-x-5 font-mono text-[10.5px] uppercase tracking-[0.08em]">
          {n > 1 && (
            <Link href={claimHref(c.slug, n - 1)} className="link text-ink-2">
              ← {claimLabel(n - 1)}
            </Link>
          )}
          <Link href={`${diligenceHref(c.slug)}#claim-${n}`} className="link text-ink">
            Claim ledger
          </Link>
          {n < total && (
            <Link href={claimHref(c.slug, n + 1)} className="link text-ink-2">
              {claimLabel(n + 1)} →
            </Link>
          )}
        </nav>
      </header>

      <div className="mt-10 grid gap-x-12 gap-y-12 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <Section n={1} id="evidence" title="Evidence">
          <ClaimEvidence claim={claim} defs={defs} demo={demo} />
        </Section>
        <Section n={2} id="assessment" title="Assessment">
          <ClaimAssessment claim={claim} defs={defs} slug={c.slug} plan={claim.test_plan} writable={writable} planOpen />
        </Section>
      </div>

      <div className="mt-16 space-y-16">
        <Section n={3} id="sources" title="Sources" right={<span className="tnum">{sources.length}</span>}>
          <Sources sources={sources} demo={demo} />
        </Section>
        <Section n={4} id="method" title="Definitions">
          <Glossary defs={defs} />
        </Section>
      </div>
    </article>
  );
}
