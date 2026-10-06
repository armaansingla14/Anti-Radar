import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { CompanyTable } from "@/components/CompanyRow";
import { FounderCard } from "@/components/FounderCard";
import { SignalTimeline } from "@/components/SignalTimeline";
import { loadFounder, loadGraph, loadLlmEnabled } from "@/components/entity/data";
import { EntityActions } from "@/components/entity/EntityActions";
import { EntityGraph } from "@/components/entity/EntityGraph";
import { EntityHeader } from "@/components/entity/EntityHeader";
import { FactsTable } from "@/components/entity/FactsTable";
import { PreCompanyMark } from "@/components/entity/FounderList";
import { PaperList, RepoTable } from "@/components/entity/Lists";
import { evidenceIndex, NarrativeBlock } from "@/components/entity/NarrativeBlock";
import { Contents, Quiet, Section } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { fmtDate, isSafeUrl } from "@/lib/format";

export async function generateMetadata({ params }: PageProps<"/founders/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const p = await loadFounder(decodeURIComponent(slug)).catch(() => null);
  return { title: p ? p.card.name : "Founder" };
}

export default async function FounderPage({ params, searchParams }: PageProps<"/founders/[slug]">) {
  const { slug } = await params;
  const sp = await searchParams;
  const detail = await loadFounder(decodeURIComponent(slug));
  if (!detail) notFound();

  const p = detail.card;
  const score = detail.founder_score;
  const [graph, llmEnabled] = await Promise.all([loadGraph(p.id), loadLlmEnabled()]);
  const evidence = evidenceIndex(score);
  const currentCo = p.current_company ? detail.companies.find((c) => c.name === p.current_company) : undefined;
  const role = p.current_role ?? p.role;

  const toc: { id: string; title: string }[] = [];
  const sec = (id: string, title: string, children: ReactNode, right?: ReactNode) => {
    toc.push({ id, title });
    return (
      <Section key={id} n={toc.length} id={id} title={title} right={right}>
        {children}
      </Section>
    );
  };

  const whyNow = sec(
    "why-now",
    "Why now",
    detail.why_now ? (
      <NarrativeBlock narrative={detail.why_now} evidence={evidence} size="lead" />
    ) : p.why_now.length > 0 ? (
      <ul className="space-y-2">
        {p.why_now.map((w, i) => (
          <li key={i} className="grid grid-cols-[16px_1fr] text-[15px] leading-snug text-ink">
            <span aria-hidden className="text-ink-3">→</span>
            {w}
          </li>
        ))}
      </ul>
    ) : (
      <Quiet>No why-now narrative yet — not enough dated signals to say what changed.</Quiet>
    ),
  );
  const facts = sec("facts", "Facts", <FactsTable facts={detail.facts} />);
  const companies = sec(
    "companies",
    "Companies",
    detail.companies.length ? (
      <CompanyTable companies={detail.companies} caption={`Companies linked to ${p.name}`} />
    ) : (
      <Quiet>
        No company on record. {p.name} is a formation-stage target: watch for a new GitHub org, domain or “starting something” post.
      </Quiet>
    ),
    <span className="tnum">{detail.companies.length}</span>,
  );
  const timeline = sec(
    "timeline",
    "Timeline",
    detail.timeline.length ? <SignalTimeline events={detail.timeline} /> : <Quiet>No dated events yet.</Quiet>,
    <span className="tnum">{detail.timeline.length} events</span>,
  );
  const research = sec("research", "Research", <PaperList papers={detail.papers} focusName={p.name} />, <span className="tnum">{detail.papers.length} papers</span>);
  const graphSec = sec(
    "graph",
    "Founder graph",
    graph ? <EntityGraph graph={graph} focalId={p.id} /> : <Quiet>The relationship graph is unavailable right now.</Quiet>,
    <span>2 hops · hover to trace · click to open</span>,
  );
  const repos = sec("repositories", "Repositories", <RepoTable repos={detail.repositories} />, <span className="tnum">{detail.repositories.length}</span>);
  const collaborators = sec(
    "collaborators",
    "Collaborators",
    detail.collaborators.length ? (
      <div className="grid gap-x-8 gap-y-2 md:grid-cols-2 xl:grid-cols-3">
        {detail.collaborators.slice(0, 9).map((c, i) => (
          <FounderCard key={c.id} person={c} rank={i + 1} />
        ))}
      </div>
    ) : (
      <Quiet>No collaborators resolved yet — co-authors and repository contributors appear here once matched.</Quiet>
    ),
    <span className="tnum">
      {detail.collaborators.length}
      {detail.collaborators.length > 9 ? " · top 9 shown" : ""}
    </span>,
  );
  const sources = sec("sources", "Sources", <Sources sources={detail.sources} />, <span className="tnum">{detail.sources.length}</span>);

  return (
    <article className="pb-10">
      <div data-card data-href={`/founders/${p.slug}`} tabIndex={-1} aria-label={`${p.name} header`}>
        <EntityHeader
          eyebrow={
            <>
              <Link href="/founders" className="label link text-ink-2">
                Founders
              </Link>
              <span aria-hidden className="h-px w-5 bg-ink-3" />
              <span className="label text-ink">{p.is_founder ? "Founder" : "Technical operator"}</span>
              {!p.current_company && <PreCompanyMark />}
              {p.is_demo && <DemoBadge note={detail.data_note} />}
              {p.first_seen_at && <span className="label text-ink-3">First seen {fmtDate(p.first_seen_at)}</span>}
            </>
          }
          name={p.name}
          lede={p.headline ?? detail.bio}
          meta={
            <div className="space-y-3">
              <p className="flex flex-wrap items-baseline gap-x-2 text-[15px] text-ink">
                {p.current_company ? (
                  <>
                    {role && <span>{role}</span>}
                    {role && <span className="text-ink-3">at</span>}
                    {currentCo ? (
                      <Link href={`/companies/${currentCo.slug}`} className="link font-serif text-[18px]">
                        {p.current_company}
                      </Link>
                    ) : (
                      <span className="font-serif text-[18px]">{p.current_company}</span>
                    )}
                  </>
                ) : (
                  <span className="text-ink-2">No current company on record — formation-stage target.</span>
                )}
              </p>
              {p.prior_affiliations.length > 0 && (
                <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-ink-2">
                  <span className="text-ink-3">Ex — </span>
                  {p.prior_affiliations.join(" · ")}
                </p>
              )}
              {detail.bio && detail.bio !== p.headline && (
                <p className="max-w-[62ch] text-[14px] leading-relaxed text-ink-2">
                  <span className="label mr-2 text-ink-3">Bio</span>“{detail.bio}”
                </p>
              )}
              <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 font-mono text-[11px] text-ink-2">
                {p.domains.length > 0 && <span className="uppercase tracking-[0.06em] text-ink-3">{p.domains.join(" · ")}</span>}
                {p.github_handle && (
                  <a href={`https://github.com/${encodeURIComponent(p.github_handle)}`} target="_blank" rel="noopener noreferrer nofollow" className="link">
                    github/{p.github_handle} ↗
                  </a>
                )}
                {p.x_handle && (
                  <a href={`https://x.com/${encodeURIComponent(p.x_handle)}`} target="_blank" rel="noopener noreferrer nofollow" className="link">
                    x/@{p.x_handle} ↗
                  </a>
                )}
                {isSafeUrl(p.personal_site) && (
                  <a href={p.personal_site} target="_blank" rel="noopener noreferrer nofollow" className="link">
                    site ↗
                  </a>
                )}
              </div>
              {p.is_demo && detail.data_note && (
                <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">{detail.data_note} — this person is invented.</p>
              )}
            </div>
          }
          score={score}
          scoreValue={p.founder_score}
          scoreLabel="Founder Score"
          history={detail.score_history}
          delta7d={p.delta_7d}
        />
        <EntityActions
          entityType="person"
          entityId={p.id}
          entityName={p.name}
          watched={detail.watched}
          autoInvestigate={sp.investigate === "1"}
          llmEnabled={llmEnabled}
        />
      </div>

      <Contents items={toc} />

      <div className="mt-12 space-y-16">
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {whyNow}
          {facts}
        </div>
        {companies}
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {timeline}
          {research}
        </div>
        {graphSec}
        {repos}
        {collaborators}
        {sources}
      </div>
    </article>
  );
}
