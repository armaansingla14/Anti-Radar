/**
 * Fictional sample props for the component gallery. Every name and URL here is
 * invented (example.com hosts only) so nothing implies facts about real
 * companies or people. All entities carry is_demo: true.
 */
import type {
  AntiScore,
  CompanySummary,
  EvidenceItem,
  IntelCard,
  InvestigateEvent,
  PersonCard,
  SourceRef,
  TimelineEvent,
  TopicTag,
} from "@/lib/types";

const ev = (
  title: string,
  source_type: string,
  event_at: string,
  detail: string | null = null,
  path = "",
): EvidenceItem => ({
  signal_id: `sig-${title.length}-${event_at}`,
  title,
  detail,
  source_type,
  source_url: `https://example.com/${source_type}/${path || title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}`,
  event_at,
  weight: null,
});

export const TAGS: TopicTag[] = [
  { key: "robotics.manipulation", theme: "robotics", label: "Dexterous manipulation", score: 0.82, method: "keyword" },
  { key: "ai.embodied", theme: "ai", label: "Embodied foundation models", score: 0.71, method: "keyword" },
  { key: "manufacturing.automation", theme: "manufacturing", label: "Factory automation", score: 0.44, method: "keyword" },
];

export const EVIDENCE: EvidenceItem[] = [
  ev("Repository stars up 312% in 30 days (184 → 758)", "github", "2026-10-03T14:00:00Z", "Commit velocity also doubled over the same window."),
  ev("Paper on tactile policy learning cites the open-source codebase", "arxiv", "2026-09-28T09:00:00Z", "Two of four authors overlap with repository maintainers."),
  ev("Careers page lists 3 new robotics controls roles", "jobs", "2026-09-30T12:00:00Z"),
  ev("Show HN thread reached 41 comments", "hackernews", "2026-09-21T17:30:00Z"),
  ev("Website changed from placeholder to product page", "website", "2026-09-25T08:00:00Z"),
];

export const SCORE: AntiScore = {
  total: 87,
  delta_7d: 18,
  delta_30d: 31,
  version: "v1.0",
  computed_on: "2026-10-06",
  feedback_adjustment: 1.5,
  cohort_size: 214,
  low_coverage: false,
  components: [
    {
      key: "technical_momentum",
      label: "Technical momentum",
      value: 22.4,
      max: 25,
      explanation: "Stars and commit velocity over 30 days rank in the 94th percentile of the cohort.",
      raw: { stars_30d: 574, commits_4w: 212, contributors: 9, percentile: 0.94 },
      evidence: EVIDENCE.slice(0, 1),
    },
    {
      key: "founder_signal",
      label: "Founder signal",
      value: 15.0,
      max: 20,
      explanation: "Two maintainers list prior roles at a tier-1 fictional robotics institute.",
      raw: { tier_1_affiliations: 2, formation_phrase: true },
      evidence: [],
    },
    {
      key: "acceleration",
      label: "Acceleration",
      value: 17.2,
      max: 20,
      explanation: "Week-over-week growth of stars accelerated for three consecutive weeks.",
      raw: { wow_growth_last: 0.41, wow_growth_prev: 0.22, weeks_accelerating: 3 },
      evidence: [],
    },
    {
      key: "thesis_fit",
      label: "Thesis fit",
      value: 12.3,
      max: 15,
      explanation: "Strong keyword match to robotics / dexterous manipulation.",
      raw: { best_topic: "robotics.manipulation", fit: 0.82 },
      evidence: [],
    },
    {
      key: "novelty",
      label: "Novelty",
      value: 8.6,
      max: 10,
      explanation: "First observed 12 days ago; novelty decays with a 45-day half-life.",
      raw: { days_since_first_seen: 12, half_life_days: 45 },
      evidence: [],
    },
    {
      key: "attention_gap",
      label: "Attention gap",
      value: 8.0,
      max: 10,
      explanation: "Technical activity is high while measured public attention remains low.",
      raw: { technical_pct: 91, attention_pct: 28 },
      evidence: [],
    },
  ],
  attention_gap: {
    technical: 91,
    attention: 28,
    gap: 63,
    flagged: true,
    coverage: "partial",
    sources: [
      { key: "hackernews", label: "Hacker News mentions", available: true, value: 2, percentile: 31 },
      { key: "github_stars", label: "GitHub stargazers", available: true, value: 758, percentile: 44 },
      { key: "x", label: "X mentions", available: false, value: null, percentile: null, note: "No API access configured" },
    ],
  },
  collision: {
    score: 0.78,
    families: ["github", "arxiv", "jobs", "hackernews", "website"],
    window_days: 42,
    span_days: 12,
    signal_count: 5,
    summary: "5 independent sources within 12 days",
    evidence: EVIDENCE,
  },
};

export const CARD: IntelCard = {
  rank: 1,
  entity_type: "company",
  id: "00000000-0000-0000-0000-000000000001",
  slug: "example-robotics",
  name: "Example Robotics",
  kind_label: "Stealth robotics company",
  one_liner: "Open-source tactile manipulation stack moving from research code toward a hardware product.",
  score: 87,
  delta_7d: 18,
  delta_30d: 31,
  thesis_tags: TAGS.slice(0, 2),
  why_surfaced: EVIDENCE,
  why_anti: [
    "Fits the robotics thesis: dexterous manipulation is a named focus area.",
    "Founders appear technical: maintainers authored the cited paper.",
    "Pre-consensus: attention percentile is far below technical percentile.",
  ],
  why_anti_epistemic: "INFERENCE",
  attention_gap: SCORE.attention_gap,
  collision: SCORE.collision,
  sparkline: [184, 190, 199, 205, 214, 230, 241, 255, 270, 292, 310, 333, 351, 378, 402, 430, 455, 478, 510, 536, 561, 590, 612, 640, 668, 690, 712, 735, 750, 758],
  sparkline_label: "GitHub stars · 30d",
  first_seen_at: "2026-09-24T00:00:00Z",
  is_new: true,
  watched: false,
  is_demo: true,
  data_note: "Fictional sample",
};

export const CARD_PERSON: IntelCard = {
  ...CARD,
  rank: 2,
  entity_type: "person",
  id: "00000000-0000-0000-0000-000000000002",
  slug: "jordan-sample",
  name: "Jordan Sample",
  kind_label: "Founder",
  one_liner: "Former controls lead at a fictional aerospace lab; new GitHub organization created last month.",
  score: 74,
  delta_7d: 4,
  delta_30d: -2,
  is_new: false,
  why_anti_epistemic: "MODEL_OPINION",
  why_anti: ["Profile resembles prior Anti Fund founders: deep systems background, building at formation."],
  attention_gap: { ...SCORE.attention_gap!, flagged: false, gap: 12, technical: 58, attention: 46, coverage: "low" },
  sparkline: [3, 3, 4, 4, 6, 5, 7, 9, 8, 11, 12, 12, 14, 16],
  sparkline_label: "Commits / week",
};

export const PERSON: PersonCard = {
  id: "00000000-0000-0000-0000-000000000003",
  slug: "avery-placeholder",
  name: "Avery Placeholder",
  headline: "Former propulsion engineer at Coriolis Aerospace (fictional)",
  current_company: "Example Fusion Works",
  current_role: "Co-founder",
  prior_affiliations: ["Coriolis Aerospace", "Solace Plasma Laboratory"],
  domains: ["energy", "frontier_infrastructure"],
  github_handle: "avery-placeholder",
  x_handle: null,
  personal_site: "https://example.com/avery",
  avatar_url: null,
  founder_score: 81,
  delta_7d: 12,
  why_now: ["Created a new GitHub organization 19 days ago", "Two former colleagues joined the same organization", "First paper as last author"],
  is_founder: true,
  role: "founder",
  is_demo: true,
  first_seen_at: "2026-09-17T00:00:00Z",
};

export const COMPANIES: CompanySummary[] = [
  {
    id: "c1",
    slug: "example-robotics",
    name: "Example Robotics",
    one_liner: "Open-source tactile manipulation stack.",
    kind: "stealth",
    stage: "pre_seed",
    score: 87,
    delta_7d: 18,
    thesis_tags: TAGS.slice(0, 1),
    attention_gap: 63,
    collision: 0.78,
    stars_30d: 574,
    first_seen_at: "2026-09-24T00:00:00Z",
    website: "https://example.com",
    github_org: null,
    is_demo: true,
    watched: true,
  },
  {
    id: "c2",
    slug: "sample-silicon",
    name: "Sample Silicon",
    one_liner: "Chiplet interconnect verification tooling.",
    kind: "project",
    stage: "unknown",
    score: 69,
    delta_7d: -3,
    thesis_tags: [{ key: "semiconductors.eda", theme: "semiconductors", label: "EDA", score: 0.6, method: "keyword" }],
    attention_gap: -8,
    collision: 0.41,
    stars_30d: 1320,
    first_seen_at: "2026-08-02T00:00:00Z",
    website: null,
    github_org: null,
    is_demo: true,
    watched: false,
  },
  {
    id: "c3",
    slug: "placeholder-grid",
    name: "Placeholder Grid",
    one_liner: null,
    kind: "company",
    stage: "seed",
    score: null,
    delta_7d: null,
    thesis_tags: [],
    attention_gap: null,
    collision: null,
    stars_30d: null,
    first_seen_at: "2026-10-05T00:00:00Z",
    website: null,
    github_org: null,
    is_demo: true,
    watched: false,
  },
];

export const TIMELINE: TimelineEvent[] = [
  { day: "2026-10-03", title: "Stars crossed 750", detail: "Up from 184 thirty days earlier.", source_type: "github", source_url: "https://example.com/github/stars", signal_type: "repo_star_velocity", epistemic: "FACT", is_demo: true },
  { day: "2026-09-30", title: "Three controls engineering roles posted", detail: null, source_type: "jobs", source_url: "https://example.com/careers", signal_type: "hiring_engineering", epistemic: "FACT", is_demo: true },
  { day: "2026-09-28", title: "Paper cites the codebase", detail: "Authors overlap with maintainers (name match, not verified identity).", source_type: "arxiv", source_url: "https://example.com/arxiv/2609.00001", signal_type: "paper_code_link", epistemic: "INFERENCE", is_demo: true },
  { day: "2025-12-14", title: "Repository created", detail: null, source_type: "github", source_url: null, signal_type: "repo_created", epistemic: "FACT", is_demo: true },
];

export const SOURCES: SourceRef[] = [
  { id: "S1", url: "https://example.com/github/example-robotics", title: "example-robotics/tactile-stack repository", source_type: "github", fetched_at: "2026-10-06T07:00:00Z" },
  { id: "S2", url: "https://example.com/arxiv/2609.00001", title: "Tactile Policy Learning at Scale (fictional)", source_type: "arxiv", fetched_at: "2026-10-05T07:00:00Z" },
  { id: "S3", url: null, title: "Analyst note", source_type: "manual", fetched_at: null },
];

export const MEMO = `# Example Robotics — first look

> Fictional sample memo for the component gallery.

## Why now

Repository stars rose from 184 to 758 in thirty days [S1], and a new paper cites the codebase [S2]. Two of four paper authors share names with maintainers — **a name match, not a verified identity**.

## Questions

- Is the hardware effort real, or is the careers page aspirational? [S1]
- Who owns the IP from the prior lab affiliation?

| Signal | Value | Source |
| --- | --- | --- |
| Stars (30d) | +574 | [S1] |
| Paper citations | 1 | [S2] |
`;

export const STREAM: InvestigateEvent[] = [
  { type: "step", step: "plan", status: "ok", message: "Planning: 4 sources to check" },
  { type: "step", step: "github", status: "ok", message: "Fetched repository metadata and 30-day stargazer series", url: "https://example.com/github/example-robotics" },
  { type: "step", step: "website", status: "ok", message: "Fetched careers page (robots.txt allows)", url: "https://example.com/careers" },
  { type: "step", step: "x", status: "skipped", message: "X provider disabled — no credentials" },
  { type: "step", step: "arxiv", status: "running", message: "Searching arXiv for maintainer names…" },
];
