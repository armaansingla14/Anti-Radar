import type { Metadata } from "next";
import { displayNow } from "@/lib/dataSource";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { DemoBadge } from "@/components/Badges";
import { CompanyTable } from "@/components/CompanyRow";
import { FounderCard } from "@/components/FounderCard";
import { SignalTimeline } from "@/components/SignalTimeline";
import { latestBriefId, loadFormationFeed, loadFounder, loadGraph, loadStatus } from "@/components/entity/data";
import {
  collisionLine,
  formationEvidence,
  formationsFor,
  sectionNumbers,
  labContributions,
  whyNowEvidence,
  xModeCopy,
  xModeFromStatus,
} from "@/components/entity/entityLogic";
import { FormationSignals, TechnicalTrajectory, XPresence } from "@/components/entity/PersonSignals";
import { ScoreCompact, WhyNowLead } from "@/components/entity/WhyNowFirst";
import { XIntelligence } from "@/components/entity/XIntelligence";
import { RelationshipList } from "@/components/formation/RelationshipList";
import { DataAge } from "@/components/DataAge";
import { Delta } from "@/components/Delta";
import { SourcesButton } from "@/components/SourceDrawer";
import { EntityActions } from "@/components/entity/EntityActions";
import { EntityGraph } from "@/components/entity/EntityGraph";
import { EntityHeader } from "@/components/entity/EntityHeader";
import { FactsTable } from "@/components/entity/FactsTable";
import { PreCompanyMark } from "@/components/entity/FounderList";
import { PaperList, RepoTable } from "@/components/entity/Lists";
import { Contents, Quiet, Section } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { HandleLinks } from "@/components/HandleLinks";
import { getReports } from "@/lib/api";
import { getSession } from "@/lib/session";
import { fmtDate, NO_COMPANY_LABEL, personKindLabel } from "@/lib/format";

export async function generateMetadata({ params }: PageProps<"/founders/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const p = await loadFounder(decodeURIComponent(slug)).catch(() => null);
  return { title: p ? p.card.name : "Person" };
}

/** Latest cached meeting brief and Anti Brief for this person (optional context — never fails the page, never generates). */
async function latestReports(personId: string): Promise<{ meeting: string | null; brief: string | null }> {
  try {
    const reports = await getReports({ entity_id: personId, limit: 50 });
    const briefs = reports.filter((r) => r.kind === "meeting_brief").sort((a, b) => b.created_at.localeCompare(a.created_at));
    return { meeting: briefs[0]?.id ?? null, brief: latestBriefId(reports) };
  } catch {
    return { meeting: null, brief: null };
  }
}

export default async function FounderPage({ params, searchParams }: PageProps<"/founders/[slug]">) {
  const { slug } = await params;
  const sp = await searchParams;
  const detail = await loadFounder(decodeURIComponent(slug));
  if (!detail) notFound();

  const p = detail.card;
  const score = detail.founder_score;
  const [graph, status, session, reportIds, formationFeed] = await Promise.all([
    loadGraph(p.id),
    loadStatus(),
    getSession(),
    latestReports(p.id),
    loadFormationFeed(),
  ]);
  const llmEnabled = typeof status?.llm?.enabled === "boolean" ? status.llm.enabled : null;
  const xRow = status?.providers.find((x) => x.provider === "x");
  const xMode = xModeFromStatus(xRow);
  const now = displayNow();
  const whyEvidence = whyNowEvidence(score, 4);
  const allEvidence = whyNowEvidence(score, 50);
  const formations = formationsFor(formationFeed, p.id);
  const formationEv = formationEvidence(score);
  const labs = labContributions(score);
  // Stored relationships served on the person (VERIFIED / INFERRED as stored). Never re-derived from the
  // graph: graph edges carry no epistemic status and would all read as INFERRED.
  const relationships = detail.relationships ?? [];
  const currentCo = p.current_company ? detail.companies.find((c) => c.name === p.current_company) : undefined;
  const role = p.current_role ?? p.role;

  const numbers = sectionNumbers([
    "why-now", "formation", "trajectory", "relationships", "facts", "timeline", "founder-score",
    "companies", "research", "graph", "repositories", "collaborators", "x", "sources",
  ]);
  const titles = new Map<string, string>();
  const sec = (id: string, title: string, children: ReactNode, right?: ReactNode) => {
    titles.set(id, title);
    return (
      <Section key={id} n={numbers.get(id)} id={id} title={title} right={right}>
        {children}
      </Section>
    );
  };

  const whyNow = sec(
    "why-now",
    "Why now",
    <WhyNowLead
      narrative={detail.why_now}
      fallback={p.why_now}
      evidence={whyEvidence}
      collision={collisionLine(score?.collision)}
      demo={p.is_demo}
      emptyText="No why-now yet — not enough dated signals to say what changed."
    />,
    allEvidence.length ? <SourcesButton claim={`Why ${p.name} is on the radar`} evidence={allEvidence} demo={p.is_demo} /> : undefined,
  );
  const formationSec = sec(
    "formation",
    "Formation signals",
    <FormationSignals formations={formations} evidence={formationEv} demo={p.is_demo} />,
    formationEv.value !== null && formationEv.max !== null ? (
      <span className="tnum">
        formation {Math.round(formationEv.value)} / {formationEv.max}
      </span>
    ) : undefined,
  );
  const trajectory = sec(
    "trajectory",
    "Technical trajectory",
    <TechnicalTrajectory labs={labs} repos={detail.repositories} papers={detail.papers} demo={p.is_demo} />,
  );
  const relSec = sec(
    "relationships",
    "Relationships",
    relationships.length ? (
      <RelationshipList relationships={relationships} demo={p.is_demo} limit={8} />
    ) : (
      <Quiet>No relationships recorded yet — co-authorship, co-contribution and shared-organization links appear once resolved.</Quiet>
    ),
    <span className="tnum">
      {relationships.length} · {relationships.filter((r) => r.epistemic === "VERIFIED").length} verified
    </span>,
  );
  const scoreSec = sec(
    "founder-score",
    "Anti Founder Score",
    <ScoreCompact score={score} value={p.founder_score} label="Founder Score" history={detail.score_history} entityName={p.name} isDemo={p.is_demo} />,
  );
  const xSec = sec(
    "x",
    "X intelligence",
    <XIntelligence mode={xMode} reason={xRow?.reason} entity={p.is_demo ? null : { type: "person", id: p.id, name: p.name }} readOnly={session.read_only} />,
  );
  const facts = sec("facts", "Facts", <FactsTable facts={detail.facts} demo={p.is_demo} />);
  const companies = sec(
    "companies",
    "Companies",
    detail.companies.length ? (
      <CompanyTable companies={detail.companies} caption={`Companies linked to ${p.name}`} />
    ) : (
      <Quiet>
        {NO_COMPANY_LABEL}. Watch for a new GitHub org, domain or “starting something” post.
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
  const sources = sec("sources", "Sources", <Sources sources={detail.sources} demo={p.is_demo} />, <span className="tnum">{detail.sources.length}</span>);

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
              <span className="label text-ink">{personKindLabel(p)}</span>
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
                  <span className="text-ink-2">{NO_COMPANY_LABEL}.</span>
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
                <HandleLinks
                  github={p.github_handle}
                  x={null}
                  site={p.personal_site}
                  isDemo={p.is_demo}
                  format={{
                    github: (h) => (p.is_demo ? `github/${h} (fictional)` : `github/${h} ↗`),
                    x: (h) => (p.is_demo ? `x/@${h} (fictional)` : `x/@${h} ↗`),
                    site: "site ↗",
                  }}
                />
              </div>
              <XPresence handle={p.x_handle} demo={p.is_demo} xLine={xModeCopy(xMode, xRow?.reason).line} />
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
          isDemo={p.is_demo}
          showScore={false}
          aside={
            <div className="space-y-3">
              <p className="flex items-baseline justify-between gap-3">
                <a href="#founder-score" className="label text-ink-2 hover:text-ink">
                  Founder Score
                </a>
                <span className="flex items-baseline gap-3">
                  <span className="tnum font-serif text-[30px] leading-none text-ink">{score?.total ?? p.founder_score ?? "—"}</span>
                  <Delta value={score?.delta_7d ?? p.delta_7d} period="7d" size="xs" />
                </span>
              </p>
              <p className="text-[12px] leading-snug text-ink-3">Secondary to the evidence — the score is decomposed further down.</p>
            </div>
          }
        />
        <EntityActions
          entityType="person"
          entityId={p.id}
          entityName={p.name}
          watched={detail.watched}
          autoInvestigate={sp.investigate === "1"}
          llmEnabled={llmEnabled}
          meetingBriefId={reportIds.meeting}
          readOnly={session.read_only}
          briefId={reportIds.brief}
        />
      </div>

      <Contents items={[...numbers.keys()].filter((id) => titles.has(id)).map((id) => ({ id, title: titles.get(id)! }))} />

      <div className="mt-12 space-y-16">
        <div className="space-y-6">
          {whyNow}
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-y border-rule py-2.5" aria-label="Data age">
            <span className="label shrink-0 text-ink">Data age</span>
            <DataAge age={detail.data_age} now={now} entityType="person" />
          </div>
        </div>
        {formationSec}
        {trajectory}
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {relSec}
          {facts}
        </div>
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {timeline}
          {scoreSec}
        </div>
        {companies}
        {research}
        {graphSec}
        {repos}
        {collaborators}
        {xSec}
        {sources}
      </div>
    </article>
  );
}
