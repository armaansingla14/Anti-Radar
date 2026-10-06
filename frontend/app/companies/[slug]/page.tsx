import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { AttentionGapPlot } from "@/components/AttentionGapPlot";
import { DemoBadge, NewBadge, VerificationBadge } from "@/components/Badges";
import { CollisionStrip } from "@/components/CollisionStrip";
import { FounderCard } from "@/components/FounderCard";
import { SignalTimeline } from "@/components/SignalTimeline";
import { ThesisTag } from "@/components/ThesisTag";
import { loadCompany, loadGraph, loadLlmEnabled } from "@/components/entity/data";
import { EntityActions } from "@/components/entity/EntityActions";
import { EntityGraph } from "@/components/entity/EntityGraph";
import { EntityHeader } from "@/components/entity/EntityHeader";
import { FactsTable } from "@/components/entity/FactsTable";
import { HiringPanel, MatchCandidates, PaperList, ReportsList, RepoTable, RiskList } from "@/components/entity/Lists";
import { Momentum } from "@/components/entity/Momentum";
import { evidenceIndex, NarrativeBlock } from "@/components/entity/NarrativeBlock";
import { Contents, Quiet, Section, stageText } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { today } from "@/lib/clock";
import { daysBetween, externalHref, fmtDate, githubUrl, humanize, isSafeUrl, parseDate } from "@/lib/format";
import type { ChartMarker } from "@/components/LineChart";

export async function generateMetadata({ params }: PageProps<"/companies/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const c = await loadCompany(decodeURIComponent(slug)).catch(() => null);
  return { title: c ? c.summary.name : "Company" };
}

export default async function CompanyPage({ params, searchParams }: PageProps<"/companies/[slug]">) {
  const { slug } = await params;
  const sp = await searchParams;
  const detail = await loadCompany(decodeURIComponent(slug));
  if (!detail) notFound();

  const c = detail.summary;
  const score = detail.anti_score;
  const [graph, llmEnabled] = await Promise.all([loadGraph(c.id), loadLlmEnabled()]);
  const evidence = evidenceIndex(score);
  const stageFact = detail.facts.find((f) => /stage/i.test(f.label));
  const isNew = (() => {
    const d = parseDate(c.first_seen_at);
    return d ? daysBetween(d, today()) <= 7 : false;
  })();

  const markers: ChartMarker[] = [
    ...detail.repositories.filter((r) => r.gh_created_at).map((r) => ({ day: r.gh_created_at!.slice(0, 10), label: "Repo" })),
    ...detail.papers.filter((p) => p.published_at).map((p) => ({ day: p.published_at!.slice(0, 10), label: "Paper" })),
  ].slice(0, 4);

  // Numbered sections, in reading order. The table of contents is built from the same list.
  const toc: { id: string; title: string }[] = [];
  const sec = (id: string, title: string, children: ReactNode, right?: ReactNode, className?: string) => {
    toc.push({ id, title });
    return (
      <Section key={id} n={toc.length} id={id} title={title} right={right} className={className}>
        {children}
      </Section>
    );
  };

  const whyNow = sec(
    "why-now",
    "Why now",
    detail.why_now ? (
      <NarrativeBlock narrative={detail.why_now} evidence={evidence} size="lead" demo={c.is_demo} />
    ) : (
      <Quiet>No why-now narrative yet — not enough dated signals to say what changed.</Quiet>
    ),
  );
  const facts = sec("facts", "Facts", <FactsTable facts={detail.facts} demo={c.is_demo} />);
  const timeline = sec(
    "timeline",
    "Signal timeline",
    detail.timeline.length ? <SignalTimeline events={detail.timeline} /> : <Quiet>No dated signals yet.</Quiet>,
    <span className="tnum">{detail.timeline.length} events</span>,
  );
  const gap = sec(
    "attention-gap",
    "Attention gap",
    score?.attention_gap ? (
      <AttentionGapPlot gap={score.attention_gap} />
    ) : (
      <Quiet>Attention gap not computed — technical or attention inputs are missing for this entity.</Quiet>
    ),
  );
  const collision = sec(
    "collision",
    "Signal collision",
    score?.collision ? (
      <CollisionStrip collision={score.collision} asOf={score.computed_on} />
    ) : (
      <Quiet>No collision — fewer than two independent source families moved inside the window.</Quiet>
    ),
  );
  const momentum = sec("momentum", "Technical momentum", <Momentum series={detail.series} markers={markers} />);
  const founders = sec(
    "team",
    "Founders & team",
    detail.founders.length ? (
      <div className="grid gap-x-8 gap-y-2 md:grid-cols-2 xl:grid-cols-3">
        {detail.founders.map((p, i) => (
          <FounderCard key={p.id} person={p} rank={i + 1} />
        ))}
      </div>
    ) : (
      <Quiet>No people linked yet. Team members appear once a GitHub org member, paper author or profile is resolved to this company.</Quiet>
    ),
    <span className="tnum">{detail.founders.length} known · lower bound</span>,
  );
  const teamGraph =
    graph && graph.nodes.length > 1
      ? sec(
          "team-graph",
          "Team graph",
          <EntityGraph graph={graph} focalId={c.id} />,
          <span>2 hops · hover to trace</span>,
        )
      : null;
  const repos = sec("repositories", "Repositories", <RepoTable repos={detail.repositories} />, <span className="tnum">{detail.repositories.length}</span>);
  const research = sec("research", "Research", <PaperList papers={detail.papers} />, <span className="tnum">{detail.papers.length} papers</span>);
  const hiring = sec("hiring", "Hiring", <HiringPanel hiring={detail.hiring} jobs={detail.jobs} demo={c.is_demo} />);
  const whyAnti = sec(
    "why-anti",
    "Why Anti Fund",
    detail.why_anti ? (
      <NarrativeBlock narrative={detail.why_anti} evidence={evidence} demo={c.is_demo} />
    ) : (
      <Quiet>No thesis rationale generated yet.</Quiet>
    ),
  );
  const risks = sec("risks", "Risks / questions", <RiskList risks={detail.risks} />, <span>deliberately skeptical</span>);
  const reports = sec("reports", "Reports", <ReportsList reports={detail.reports} />, <span className="tnum">{detail.reports.length}</span>);
  const dupes = sec("duplicates", "Possible duplicates", <MatchCandidates candidates={detail.match_candidates} basePath="/companies" />);
  const sources = sec("sources", "Sources", <Sources sources={detail.sources} demo={c.is_demo} />, <span className="tnum">{detail.sources.length}</span>);

  // Demo companies never link out to real hosts (github.com etc.); their
  // identifiers still render as plain text, labelled fictional.
  const links: { href: string | null; text: string }[] = [];
  if (isSafeUrl(c.website)) links.push({ href: externalHref(c.website, c.is_demo), text: c.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "") });
  if (c.github_org) links.push({ href: githubUrl(c.github_org, c.is_demo), text: `github/${c.github_org}` });

  return (
    <article className="pb-10">
      <div data-card data-href={`/companies/${c.slug}`} tabIndex={-1} aria-label={`${c.name} header`}>
        <EntityHeader
          eyebrow={
            <>
              <Link href="/companies" className="label link text-ink-2">
                Companies
              </Link>
              <span aria-hidden className="h-px w-5 bg-ink-3" />
              <span className="label text-ink">{humanize(c.kind)}</span>
              {isNew && <NewBadge />}
              {c.is_demo && <DemoBadge note={detail.data_note} />}
              {c.first_seen_at && <span className="label text-ink-3">First seen {fmtDate(c.first_seen_at)}</span>}
            </>
          }
          name={c.name}
          lede={c.one_liner ?? detail.description}
          meta={
            <div className="space-y-4">
              {c.thesis_tags.length > 0 && (
                <div className="flex flex-wrap gap-x-4 gap-y-2" aria-label="Thesis tags">
                  {c.thesis_tags.map((t) => (
                    <ThesisTag key={t.key} tag={t} href="/thesis" />
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                <span className="inline-flex flex-wrap items-center gap-2">
                  <span className="label">Stage estimate</span>
                  <span className="font-serif text-[17px] text-ink">{stageText(c.stage)}</span>
                  <VerificationBadge value={stageFact?.status ?? (c.stage === "unknown" ? "UNKNOWN" : "INFERRED")} />
                  {stageFact?.note && <span className="text-[12px] text-ink-3">— {stageFact.note}</span>}
                </span>
                {links.map((l) =>
                  l.href ? (
                    <a key={l.text} href={l.href} target="_blank" rel="noopener noreferrer nofollow" className="link font-mono text-[11px] text-ink-2">
                      {l.text} ↗
                    </a>
                  ) : (
                    <span key={l.text} className="font-mono text-[11px] text-ink-3" title="Fictional identifier — not linked">
                      {l.text} (fictional)
                    </span>
                  ),
                )}
              </div>
              {c.is_demo && detail.data_note && (
                <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">{detail.data_note} — names, people and links are invented.</p>
              )}
            </div>
          }
          score={score}
          scoreValue={c.score}
          scoreLabel="Anti Score"
          history={detail.score_history}
          delta7d={c.delta_7d}
          isDemo={c.is_demo}
        />
        <EntityActions
          entityType="company"
          entityId={c.id}
          entityName={c.name}
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
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {timeline}
          <div className="space-y-14">
            {gap}
            {collision}
          </div>
        </div>
        {momentum}
        {founders}
        {teamGraph}
        {repos}
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {research}
          {hiring}
        </div>
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {whyAnti}
          {risks}
        </div>
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-2">
          {reports}
          {dupes}
        </div>
        {sources}
      </div>
    </article>
  );
}
