import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { cache } from "react";
import { DemoBadge } from "@/components/Badges";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SignalTimeline } from "@/components/SignalTimeline";
import { WatchButton } from "@/components/WatchButton";
import { EntityGraph } from "@/components/entity/EntityGraph";
import { HiringPanel, PaperList, RepoTable } from "@/components/entity/Lists";
import { Contents, Quiet, Section } from "@/components/entity/primitives";
import { Sources } from "@/components/entity/Sources";
import { WhyButton } from "@/components/entity/WhyButton";
import { ComponentBars, ConvergenceSummary } from "@/components/formation/Convergence";
import { MemberLine } from "@/components/formation/FormationCardView";
import { FormationLabel, ThemeLine } from "@/components/formation/Labels";
import { relationshipGraph, tierTitle, toAntiScore, watchTarget } from "@/components/formation/logic";
import { RelationshipList } from "@/components/formation/RelationshipList";
import { SignalStrip } from "@/components/formation/SignalStrip";
import { TeamGrid } from "@/components/formation/TeamGrid";
import { WhyNow } from "@/components/formation/WhyNow";
import { getFormationDetail, getFormationScore, orNull } from "@/lib/api";

const loadDetail = cache((slug: string) => orNull(getFormationDetail(slug)));

export async function generateMetadata({ params }: PageProps<"/formation/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const d = await loadDetail(decodeURIComponent(slug)).catch(() => null);
  return { title: d ? `${d.card.title} · Formation` : "Formation" };
}

export default async function FormationReportPage({ params }: PageProps<"/formation/[slug]">) {
  const { slug } = await params;
  const detail = await loadDetail(decodeURIComponent(slug));
  if (!detail) notFound();

  const card = detail.card;
  // Breakdown for "Why N?": the score endpoint, else the components already on the card (same source).
  const rawScore = await getFormationScore(card.id).catch(() => null);
  const score = toAntiScore(rawScore, card);
  const watch = watchTarget(card.anchor);
  const graph = relationshipGraph(detail.relationships, card.anchor, card.members);
  const personTies = detail.relationships.filter((r) => r.src.type === "person" && r.dst.type === "person").length;

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
    card.why_now.length ? (
      <WhyNow items={card.why_now} size="lead" demo={card.is_demo} />
    ) : (
      <Quiet>No dated formation evidence recorded — this candidate rests on relationships alone.</Quiet>
    ),
    <span className="tnum">{card.why_now.length} dated</span>,
  );
  const anatomy = sec(
    "score",
    "Formation score",
    <div>
      <ComponentBars components={card.components} />
      <p className="mt-4 text-[12.5px] leading-relaxed text-ink-3">
        Deterministic: every component is computed from the cited evidence; no language model contributes to the score.
      </p>
    </div>,
    <span className="tnum">{card.score} / 100</span>,
  );
  const team = sec(
    "team",
    "Team",
    card.members.length ? <TeamGrid members={card.members} /> : <Quiet>No people resolved for this candidate yet.</Quiet>,
    <span className="tnum">{card.members.length} {card.members.length === 1 ? "person" : "people"}</span>,
  );
  const convergence = sec(
    "convergence",
    "Team convergence",
    <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <ConvergenceSummary c={detail.convergence} />
      {detail.relationships.length ? (
        <RelationshipList relationships={detail.relationships} demo={card.is_demo} />
      ) : (
        <Quiet>No recorded relationships between these people — co-authorship, shared repositories and shared organisations appear here once observed.</Quiet>
      )}
    </div>,
    <span className="tnum">{detail.relationships.length} relationships</span>,
  );
  const graphSec = sec(
    "graph",
    "Relationship graph",
    graph.edges.length ? (
      <div className="max-w-[880px]">
        <EntityGraph graph={graph} focalId={card.anchor.id} maxNodes={28} />
      </div>
    ) : (
      <Quiet>Nothing to draw yet — no recorded links between these entities.</Quiet>
    ),
    <span>
      <span className="tnum">{personTies}</span> person–person {personTies === 1 ? "tie" : "ties"} · hover to trace · click to open
    </span>,
  );
  const timeline = sec(
    "timeline",
    "Signal timeline",
    detail.timeline.length ? <SignalTimeline events={detail.timeline} /> : <Quiet>No dated events yet.</Quiet>,
    <span className="tnum">{detail.timeline.length} events</span>,
  );
  const hiring = sec(
    "hiring",
    "Hiring",
    <HiringPanel hiring={{}} jobs={detail.jobs} demo={card.is_demo} />,
    <span className="tnum">{detail.jobs.length} roles</span>,
  );
  const repos = sec("repositories", "Repositories", <RepoTable repos={detail.repositories} />, <span className="tnum">{detail.repositories.length}</span>);
  const research = sec("research", "Research", <PaperList papers={detail.papers} />, <span className="tnum">{detail.papers.length} papers</span>);
  const sources = sec("sources", "Sources", <Sources sources={detail.sources} demo={card.is_demo} />, <span className="tnum">{detail.sources.length}</span>);

  return (
    <article className="pb-10">
      <div data-card data-href={`/formation/${card.slug}`} tabIndex={-1} aria-label={`${card.title} header`}>
        <header className="grid grid-cols-12 gap-x-10 gap-y-8 pb-7">
          <div className="col-span-12 min-w-0 lg:col-span-8">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <Link href="/formation" className="label link text-ink-2">
                Formation Radar
              </Link>
              <span aria-hidden className="h-px w-5 bg-ink-3" />
              <FormationLabel label={card.label} tier={card.tier} />
              <ThemeLine theme={card.theme_label} subtopic={card.subtopic_label} />
              {card.is_demo && <DemoBadge />}
            </div>
            <h1 className="mt-4 font-serif text-[56px] leading-[0.98] tracking-[-0.02em] text-ink max-md:text-[42px]">{card.title}</h1>
            <MemberLine card={card} className="mt-4 text-[15px]" />
            <SignalStrip card={card} className="mt-5" />
            <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2">
              {watch && <WatchButton entityType={watch.entityType} entityId={watch.entityId} watched={false} size="md" />}
              {card.anchor.type === "company" && card.anchor.slug && (
                <Link href={`/companies/${card.anchor.slug}`} className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink-2">
                  Anchor · {card.anchor.name} →
                </Link>
              )}
            </div>
          </div>
          <aside
            className="col-span-12 flex min-w-0 flex-col gap-4 border-t border-ink pt-4 lg:col-span-4 lg:border-l lg:border-t-0 lg:border-rule lg:pl-8 lg:pt-1"
            aria-label={`Formation Score ${card.score}`}
          >
            <ScoreNumeral value={card.score} size="xl" accent label="Formation Score" className="[&>span:last-child]:mt-2" />
            <p className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2">
              Tier · <span className="text-ink">{tierTitle(card.tier)}</span>
              <span className="mx-2 text-ink-3">·</span>
              <span className="tnum">{card.independent_signals}</span> independent signals
            </p>
            <WhyButton score={score} entityName={card.title} scoreLabel="Formation Score" isDemo={card.is_demo} className="self-start" />
          </aside>
        </header>
      </div>

      <Contents items={toc} />

      <div className="mt-12 space-y-16">
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {whyNow}
          {anatomy}
        </div>
        {team}
        {convergence}
        {graphSec}
        <div className="grid gap-x-12 gap-y-14 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          {timeline}
          {hiring}
        </div>
        {repos}
        {research}
        {sources}
      </div>
    </article>
  );
}
