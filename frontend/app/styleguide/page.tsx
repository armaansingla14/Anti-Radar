import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AttentionGapMini, AttentionGapPlot } from "@/components/AttentionGapPlot";
import { DemoBadge, EpistemicBadge, NewBadge, VerificationBadge } from "@/components/Badges";
import { BarSeries } from "@/components/BarSeries";
import { CollisionStrip } from "@/components/CollisionStrip";
import { CompanyTable } from "@/components/CompanyRow";
import { Delta } from "@/components/Delta";
import { EmptyState } from "@/components/EmptyState";
import { EvidenceList } from "@/components/EvidenceList";
import { FounderCard } from "@/components/FounderCard";
import { LineChart } from "@/components/LineChart";
import { Markdown } from "@/components/Markdown";
import { ScoreBar } from "@/components/ScoreBar";
import { ScoreNumeral } from "@/components/ScoreNumeral";
import { SectionLabel } from "@/components/SectionLabel";
import { SignalTimeline } from "@/components/SignalTimeline";
import { Skeleton, SkeletonCard } from "@/components/Skeleton";
import { SourceList } from "@/components/SourceList";
import { Sparkline } from "@/components/Sparkline";
import { StatTile } from "@/components/StatTile";
import { ThesisTag } from "@/components/ThesisTag";
import { CardDemos, ControlsDemo, DrawerDemo, StreamDemo } from "./demos";
import { CARD, COMPANIES, EVIDENCE, MEMO, PERSON, SCORE, SOURCES, TAGS, TIMELINE } from "./samples";

export const metadata: Metadata = { title: "Styleguide" };

function series(days: number, start: number, growth: number, wobble: number) {
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.UTC(2026, 8, 7 + i));
    return { day: d.toISOString().slice(0, 10), value: Math.round(start * (1 + growth) ** i + Math.sin(i * 1.3) * wobble) };
  });
}

function Block({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="mb-16">
      <SectionLabel index={n}>{title}</SectionLabel>
      <div className="mt-6">{children}</div>
    </section>
  );
}

export default function Styleguide() {
  return (
    <div>
      <header className="mb-14 max-w-[720px]">
        <p className="label mb-3">Internal · Component gallery</p>
        <h1 className="font-serif text-[48px] leading-[1.02] tracking-[-0.015em]">Styleguide</h1>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
          Every reusable component with fictional sample props. Names such as “Example Robotics” are invented and all
          links point to example.com. <DemoBadge />
        </p>
      </header>

      <Block n={1} title="Type & numerals">
        <div className="flex flex-wrap items-end gap-10">
          <ScoreNumeral value={87} size="xl" accent label="Anti score" />
          <ScoreNumeral value={74} size="lg" label="Founder score" />
          <ScoreNumeral value={62} size="md" outOf={100} />
          <ScoreNumeral value={41} size="sm" />
          <ScoreNumeral value={null} size="md" label="Unscored" />
        </div>
        <div className="mt-8 flex flex-wrap items-baseline gap-8">
          <Delta value={18} period="this week" size="md" />
          <Delta value={6} period="this week" />
          <Delta value={-4} period="this week" />
          <Delta value={0} period="7d" />
          <Delta value={null} />
        </div>
      </Block>

      <Block n={2} title="Stat tiles">
        <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
          <StatTile value={23} label="Emerging companies" sub={<Delta value={5} period="vs last week" size="xs" />} />
          <StatTile value={11} label="Founders surfacing" />
          <StatTile value={4} label="Strange signals" accent />
          <StatTile value="1,204" label="Signals · 24h" sub="across 6 providers" />
        </div>
      </Block>

      <Block n={3} title="Badges & tags">
        <div className="flex flex-wrap items-center gap-3">
          <EpistemicBadge value="FACT" />
          <EpistemicBadge value="INFERENCE" />
          <EpistemicBadge value="MODEL_OPINION" />
          <EpistemicBadge value="FACT" compact />
          <span className="w-4" />
          <VerificationBadge value="VERIFIED" />
          <VerificationBadge value="INFERRED" />
          <VerificationBadge value="UNKNOWN" />
          <span className="w-4" />
          <DemoBadge />
          <NewBadge />
        </div>
        <div className="mt-5 flex flex-wrap gap-4">
          {TAGS.map((t) => (
            <ThesisTag key={t.key} tag={t} />
          ))}
          <ThesisTag tag={TAGS[0]} showTheme={false} href="/thesis" />
        </div>
      </Block>

      <Block n={4} title="Intel cards">
        <CardDemos />
      </Block>

      <Block n={5} title="Why drawer">
        <DrawerDemo />
      </Block>

      <Block n={6} title="Score components">
        <div className="grid max-w-[520px] gap-4">
          {SCORE.components.map((c) => (
            <ScoreBar key={c.key} value={c.value} max={c.max} label={c.label} showValue />
          ))}
          <ScoreBar value={8} max={10} label="Accent variant" showValue accent />
        </div>
      </Block>

      <Block n={7} title="Attention gap & collision">
        <div className="grid gap-12 md:grid-cols-2">
          <AttentionGapPlot gap={SCORE.attention_gap!} />
          <div className="space-y-8">
            <CollisionStrip collision={SCORE.collision!} asOf="2026-10-06" />
            <AttentionGapMini gap={SCORE.attention_gap!} />
          </div>
        </div>
      </Block>

      <Block n={8} title="Charts">
        <div className="grid gap-12 md:grid-cols-2">
          <div>
            <p className="label mb-3">Line chart · single series</p>
            <LineChart series={[{ label: "GitHub stars", points: series(30, 184, 0.05, 6) }]} markers={[{ day: "2026-09-28", label: "Paper" }]} />
          </div>
          <div>
            <p className="label mb-3">Line chart · observed vs backfill</p>
            <LineChart
              series={[
                { label: "Score", points: series(30, 52, 0.017, 2) },
                { label: "Cohort median", points: series(30, 48, 0.002, 1), source: "backfill" },
              ]}
              note="Cohort median reconstructed from backfill; dashed."
            />
          </div>
          <div>
            <p className="label mb-3">Bar series · commits per week</p>
            <BarSeries
              data={[12, 18, 9, 22, 31, 28, 40, 46].map((v, i) => ({ label: `W${i + 32}`, value: v }))}
              highlightLast
              ariaLabel="Commits per week"
            />
          </div>
          <div>
            <p className="label mb-3">Sparklines</p>
            <div className="flex flex-wrap gap-8">
              <Sparkline values={CARD.sparkline} label="Stars · 30d" />
              <Sparkline values={[5, 4, 6, 3, 2, 3, 2]} label="Mentions · 7d" accentLast={false} />
              <Sparkline values={[3]} label="Too short" />
            </div>
          </div>
        </div>
      </Block>

      <Block n={9} title="Evidence & timeline">
        <div className="grid gap-12 md:grid-cols-2">
          <EvidenceList items={EVIDENCE} />
          <SignalTimeline events={TIMELINE} />
        </div>
      </Block>

      <Block n={10} title="Founder card">
        <div className="grid gap-8 md:grid-cols-3">
          <FounderCard person={PERSON} rank={1} />
          <FounderCard person={{ ...PERSON, id: "p2", slug: "riley-example", name: "Riley Example", founder_score: null, delta_7d: null, why_now: [], is_founder: false, prior_affiliations: [] }} rank={2} />
        </div>
      </Block>

      <Block n={11} title="Company table">
        <CompanyTable companies={COMPANIES} caption="Sample companies" />
      </Block>

      <Block n={12} title="Memo markdown & sources">
        <div className="grid gap-12 md:grid-cols-[1fr_340px]">
          <Markdown>{MEMO}</Markdown>
          <div>
            <p className="label mb-3">Sources</p>
            <SourceList sources={SOURCES} />
          </div>
        </div>
      </Block>

      <Block n={13} title="Agent trace">
        <StreamDemo />
      </Block>

      <Block n={14} title="Controls & feedback">
        <ControlsDemo />
      </Block>

      <Block n={15} title="Empty & loading states">
        <EmptyState title="No founders surfaced today." action={<a className="link label text-ink" href="/status">Check provider status →</a>}>
          Nothing crossed the threshold since your last visit. That is a real answer, not a loading error.
        </EmptyState>
        <div className="mt-8">
          <Skeleton lines={3} className="max-w-[480px]" />
          <SkeletonCard />
        </div>
      </Block>
    </div>
  );
}
