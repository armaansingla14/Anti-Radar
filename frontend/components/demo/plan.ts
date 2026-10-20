/**
 * The /demo tour plan: which real entities each scene shows, resolved from whatever data the backend
 * holds (the verified snapshot in production). Pure functions over API payloads, so the tour works on any
 * snapshot and can be unit-tested; nothing here hard-codes a name.
 */
import type { CompanySummary, FormationCard, FormationFeed, HomeFeed, MeetCard, MeetFeed, ReportOut } from "../../lib/types";

export type SceneKey =
  | "title"
  | "home"
  | "formation"
  | "collision"
  | "why-now"
  | "person"
  | "meet"
  | "attention"
  | "brief"
  | "final";

/** One stop of the tour. A scene has one or more steps (e.g. list → detail). */
export interface DemoStep {
  scene: number;
  scene_key: SceneKey;
  /** Short scene name for the presenter bar. */
  title: string;
  /** One quiet line describing what is on screen (built from data). */
  caption: string;
  /** App URL shown in the frame; null for the full-bleed cards (title, final) and not-yet-created briefs. */
  url: string | null;
  /** Pathname prefix that means "the frame is where this step expects" (used for clicked navigations). */
  match: string | null;
  /** Element to bring into view and mark once the page is ready. */
  focus: string | null;
  /** Pixels left above the focused element (more for list rows, so the page heading stays in view). */
  focus_offset?: number;
  /** On arrival at a new page, hold at the top this long (ms) before scrolling to `focus` — shows who/what first. */
  hold_top?: number;
  /** Before leaving the current page, briefly mark this element (e.g. the Investigate button the brief came from). */
  cue?: string | null;
  /**
   * Arrive by clicking this element in the current page instead of routing directly (only when moving forward
   * from the previous step and the element exists) — the real app's own action, e.g. Generate meeting brief.
   */
  click: string | null;
  /** Element whose presence means the page has rendered. */
  ready: string | null;
  /** Wait for an in-page process to finish (the streamed Anti Brief) before the step counts as settled. */
  settle?: "investigation" | null;
}

export interface LabMember {
  person_id: string;
  slug: string;
  name: string;
  commits: number;
  top_repo: string | null;
  candidate_slug: string;
}

export interface DemoPlan {
  as_of: string | null;
  is_snapshot: boolean;
  steps: DemoStep[];
  /** Entities chosen, for the script / screenshots / tests. */
  picks: {
    candidate: { slug: string; title: string; score: number; precise: number; tier: string; families: string[]; independent_signals: number } | null;
    person: LabMember | null;
    meet: { person_id: string; slug: string; name: string; priority: string; brief_report_id: string | null } | null;
    company: { id: string; slug: string; name: string; attention_gap: number | null } | null;
    brief: { report_id: string | null; company_slug: string | null; mode: "existing" | "investigate" | "none" } | null;
  };
  counts: { formations: number; people: number; signals: number | null };
}

export const SCENE_COUNT = 10;

/** Precise formation score: the sum of component values (the card's `score` is rounded, so ties are common). */
export function preciseScore(card: Pick<FormationCard, "score" | "components">): number {
  const sum = (card.components ?? []).reduce((acc, c) => acc + (Number.isFinite(c.value) ? c.value : 0), 0);
  return sum > 0 ? Math.round(sum * 100) / 100 : card.score;
}

/** All non-demo candidates, best first (precise score, then rounded score, then feed order). */
export function rankCandidates(feed: FormationFeed | null): FormationCard[] {
  if (!feed) return [];
  const all = [...(feed.high ?? []), ...(feed.emerging ?? []), ...(feed.early ?? [])];
  const real = all.filter((c) => !c.is_demo);
  const pool = real.length ? real : all;
  return pool
    .map((c, i) => ({ c, i, p: preciseScore(c) }))
    .sort((a, b) => b.p - a.p || b.c.score - a.c.score || a.i - b.i)
    .map((x) => x.c);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/**
 * People with verified frontier-lab commits, read from each candidate's Founder Quality evidence
 * (`raw.lab_members[].lab_contributions[]`), most verified commits first. Slugs come from the card members.
 */
export function labMembers(cards: FormationCard[]): LabMember[] {
  const byId = new Map<string, LabMember>();
  for (const card of cards) {
    const slugs = new Map(card.members.map((m) => [m.id, m.slug]));
    for (const comp of card.components ?? []) {
      const raw = asRecord(comp.raw);
      const members = Array.isArray(raw?.lab_members) ? (raw!.lab_members as unknown[]) : [];
      for (const m of members) {
        const rec = asRecord(m);
        const id = typeof rec?.person_id === "string" ? rec.person_id : null;
        const slug = id ? slugs.get(id) : undefined;
        if (!rec || !id || !slug) continue;
        const contribs = (Array.isArray(rec.lab_contributions) ? rec.lab_contributions : []).map(asRecord).filter(Boolean) as Record<string, unknown>[];
        let commits = 0;
        let top: { repo: string; n: number } | null = null;
        for (const c of contribs) {
          const n = typeof c.commits === "number" ? c.commits : 0;
          commits += n;
          const repo = typeof c.full_name === "string" ? c.full_name : null;
          if (repo && (!top || n > top.n)) top = { repo, n };
        }
        if (commits <= 0) continue;
        const prev = byId.get(id);
        if (!prev || prev.commits < commits) {
          byId.set(id, { person_id: id, slug, name: typeof rec.name === "string" ? rec.name : slug, commits, top_repo: top?.repo ?? null, candidate_slug: card.slug });
        }
      }
    }
  }
  return [...byId.values()].sort((a, b) => b.commits - a.commits || a.name.localeCompare(b.name));
}

/**
 * Scene 6 person: the strongest verified lab contributor, preferring someone other than the top candidate's
 * anchor (who already has three scenes) so the tour shows a second thread; falls back to that anchor, then to
 * the meet list's #1.
 */
export function pickPerson(cards: FormationCard[], top: FormationCard | null, meetTop: MeetCard | null): LabMember | null {
  const lab = labMembers(cards);
  const anchorId = top?.anchor?.type === "person" ? top.anchor.id : null;
  const other = lab.find((m) => m.person_id !== anchorId);
  if (other) return other;
  if (lab[0]) return lab[0];
  if (meetTop) {
    return { person_id: meetTop.person.id, slug: meetTop.person.slug, name: meetTop.person.name, commits: 0, top_repo: null, candidate_slug: "" };
  }
  return null;
}

/**
 * Scene 8 company: the largest attention gap among companies that are also formation anchors (they passed the
 * formation noise filters), else the largest gap among companies with an Anti Score ≥ 40, else the largest gap.
 */
export function pickAttentionCompany(companies: CompanySummary[], cards: FormationCard[]): CompanySummary | null {
  const measured = companies.filter((c) => !c.is_demo && typeof c.attention_gap === "number" && c.attention_gap > 0);
  if (!measured.length) return null;
  const sorted = [...measured].sort((a, b) => (b.attention_gap ?? 0) - (a.attention_gap ?? 0));
  const anchors = new Set(cards.filter((c) => c.anchor?.type === "company" && c.anchor.slug).map((c) => c.anchor.slug as string));
  return sorted.find((c) => anchors.has(c.slug)) ?? sorted.find((c) => (c.score ?? 0) >= 40) ?? sorted[0];
}

/** Scene 9: an existing Anti Brief on the scene-8 company, else any real Anti Brief, else investigate live. */
export function pickBrief(briefs: ReportOut[], company: CompanySummary | null): DemoPlan["picks"]["brief"] {
  const real = briefs.filter((r) => r.kind === "brief" && !r.is_demo && r.status !== "error");
  const newest = (rs: ReportOut[]) => [...rs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const own = company ? newest(real.filter((r) => r.entity_id === company.id || (r.entity_slug && r.entity_slug === company.slug))) : undefined;
  if (own) return { report_id: own.id, company_slug: company?.slug ?? null, mode: "existing" };
  if (company) return { report_id: null, company_slug: company.slug, mode: "investigate" };
  const any = newest(real);
  if (any) return { report_id: any.id, company_slug: any.entity_slug, mode: "existing" };
  return { report_id: null, company_slug: null, mode: "none" };
}

/** "2026-10-20" → "20 Oct 2026". */
export function fmtDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

const TIER_WORD: Record<string, string> = { high: "high", emerging: "emerging", early: "early signal" };

function familyText(families: string[]): string {
  const name: Record<string, string> = { github: "GitHub", research: "Research", hiring: "Hiring", web: "Web", community: "Community", social: "Social" };
  return families.map((f) => name[f] ?? f).join(" · ");
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export interface PlanInputs {
  home: HomeFeed | null;
  formation: FormationFeed | null;
  meet: MeetFeed | null;
  companies: CompanySummary[];
  briefs: ReportOut[];
  /** Existing meeting briefs for the meet #1 person (GET /reports?entity_id=…&kind=meeting_brief). */
  meetingBriefs: ReportOut[];
}

/** Build the ten-scene plan from API payloads. Scenes whose entity is missing degrade to the list page. */
export function buildPlan(inp: PlanInputs): DemoPlan {
  const cards = rankCandidates(inp.formation);
  const top = cards[0] ?? null;
  const meetTop = [...(inp.meet?.people ?? [])].sort((a, b) => a.rank - b.rank)[0] ?? null;
  const person = pickPerson(cards, top, meetTop);
  const company = pickAttentionCompany(inp.companies, cards);
  const brief = pickBrief(inp.briefs, company);
  const meetingBrief =
    meetTop?.brief_report_id ??
    [...inp.meetingBriefs].filter((r) => r.kind === "meeting_brief" && r.status !== "error").sort((a, b) => b.created_at.localeCompare(a.created_at))[0]?.id ??
    null;
  const summary = inp.home?.summary;
  const formationUrl = top ? `/formation/${encodeURIComponent(top.slug)}` : null;
  const companyUrl = company ? `/companies/${encodeURIComponent(company.slug)}` : null;

  const s = (step: Omit<DemoStep, "match" | "click" | "ready" | "focus"> & Partial<DemoStep>): DemoStep => ({
    match: step.url ? step.url.split("?")[0] : null,
    click: null,
    ready: null,
    focus: null,
    ...step,
  });

  const steps: DemoStep[] = [
    s({ scene: 1, scene_key: "title", title: "Anti Radar", caption: "Conviction Before Consensus.", url: null }),
    s({
      scene: 2,
      scene_key: "home",
      title: "The radar",
      caption: summary
        ? `${plural(summary.possible_formations, "possible formation")} · ${plural(summary.people_to_meet, "person", "people")} to meet · ${plural(summary.signal_collisions, "signal collision")} — data as of ${fmtDay(summary.data_as_of) ?? "today"}`
        : "What changed, ranked by evidence — not by fame.",
      url: "/",
      ready: '[data-demo="home-counts"]',
      focus: '[data-demo="home-counts"]',
    }),
    s({
      scene: 3,
      scene_key: "formation",
      title: "Formation Radar",
      caption: top
        ? `${plural(cards.length, "candidate")} — the strongest is ${top.anchor?.name ?? top.title}, Formation Score ${top.score}`
        : "No formation candidates in this dataset yet.",
      url: "/formation",
      ready: top ? `[data-demo="formation-card"][data-demo-slug="${top.slug}"]` : "h1",
      focus: top ? `[data-demo="formation-card"][data-demo-slug="${top.slug}"]` : null,
      focus_offset: 150,
    }),
  ];
  if (top && formationUrl) {
    steps.push(
      s({
        scene: 3,
        scene_key: "formation",
        title: "Formation Radar",
        caption: `${top.title} — Formation Score ${top.score}, ${TIER_WORD[top.tier] ?? top.tier}`,
        url: formationUrl,
        click: `[data-demo="formation-card"][data-demo-slug="${top.slug}"] [data-demo="formation-open"]`,
        ready: "#why-now",
      }),
      s({
        scene: 4,
        scene_key: "collision",
        title: "Signal collision",
        caption:
          top.families.length >= 2
            ? `${plural(top.independent_signals, "independent signal")} across ${familyText(top.families)}`
            : `${plural(top.independent_signals, "independent signal")}, one source family (${familyText(top.families) || "none"}) — not yet a collision`,
        url: formationUrl,
        ready: "#convergence",
        focus: "#convergence",
      }),
      s({
        scene: 5,
        scene_key: "why-now",
        title: "Why now",
        caption: top.why_now[0] ? `Dated evidence — ${top.why_now[0].title}` : "Dated evidence, oldest to newest.",
        url: formationUrl,
        ready: "#timeline",
        focus: "#timeline",
      }),
    );
  } else {
    steps.push(
      s({ scene: 4, scene_key: "collision", title: "Signal collision", caption: "No candidate to inspect in this dataset.", url: "/formation", ready: "h1" }),
      s({ scene: 5, scene_key: "why-now", title: "Why now", caption: "No candidate to inspect in this dataset.", url: "/formation", ready: "h1" }),
    );
  }
  steps.push(
    person
      ? s({
          scene: 6,
          scene_key: "person",
          title: "The person",
          caption: person.top_repo
            ? `${person.name} — ${person.commits.toLocaleString("en-US")} verified commits to frontier-lab repositories (${person.top_repo})`
            : `${person.name}`,
          url: `/founders/${encodeURIComponent(person.slug)}`,
          ready: "#why-now",
          focus: "#trajectory",
          hold_top: 2600,
        })
      : s({ scene: 6, scene_key: "person", title: "The person", caption: "No verified lab contributors in this dataset.", url: "/founders", ready: "h1" }),
    s({
      scene: 7,
      scene_key: "meet",
      title: "Who should we meet",
      caption: meetTop
        ? `Week ${inp.meet?.week ?? ""} — #1 is ${meetTop.person.name}, ${meetTop.priority} priority`
        : "No recommendations stored for this week.",
      url: "/meet",
      ready: meetTop ? '[data-demo="meet-row"][data-demo-rank="1"]' : "h1",
      focus: meetTop ? '[data-demo="meet-row"][data-demo-rank="1"]' : null,
      focus_offset: 150,
    }),
  );
  if (meetTop) {
    steps.push(
      s({
        scene: 7,
        scene_key: "meet",
        title: "Meeting brief",
        caption: `Meeting brief — ${meetTop.person.name}: what to ask, what to verify, every claim cited`,
        url: meetingBrief ? `/reports/${encodeURIComponent(meetingBrief)}` : null,
        match: "/reports/",
        click: '[data-demo="meet-row"][data-demo-rank="1"] [data-demo="meeting-brief"]',
        ready: '[data-demo="report"][data-demo-kind="meeting_brief"]',
      }),
    );
  }
  steps.push(
    company && companyUrl
      ? s({
          scene: 8,
          scene_key: "attention",
          title: "Attention gap",
          caption: `${company.name} — attention gap ${Math.round(company.attention_gap ?? 0)}: technical momentum far ahead of public attention`,
          url: companyUrl,
          ready: "#attention-gap",
          focus: "#attention-gap",
          hold_top: 2600,
        })
      : s({ scene: 8, scene_key: "attention", title: "Attention gap", caption: "No measured attention gap in this dataset.", url: "/companies", ready: "h1" }),
  );
  if (brief?.mode === "existing" && brief.report_id) {
    steps.push(
      s({
        scene: 9,
        scene_key: "brief",
        title: "Anti Brief",
        caption: "Investigate → Anti Brief: an analyst run over the evidence, every claim cited",
        url: `/reports/${encodeURIComponent(brief.report_id)}`,
        ready: '[data-demo="report"]',
        cue: brief.company_slug === company?.slug ? '[data-demo="investigate"]' : null,
      }),
    );
  } else if (brief?.mode === "investigate" && companyUrl) {
    steps.push(
      s({
        scene: 9,
        scene_key: "brief",
        title: "Anti Brief",
        caption: "Investigate — the analyst gathers the evidence and writes an Anti Brief, every claim cited",
        url: companyUrl,
        click: '[data-demo="investigate"]',
        ready: "#investigation",
        focus: "#investigation",
        settle: "investigation",
      }),
    );
  } else {
    steps.push(s({ scene: 9, scene_key: "brief", title: "Anti Brief", caption: "No company to investigate in this dataset.", url: "/reports", ready: "h1" }));
  }
  steps.push(s({ scene: 10, scene_key: "final", title: "Conviction Before Consensus", caption: "", url: null }));

  return {
    as_of: inp.formation?.as_of ?? summary?.data_as_of ?? null,
    is_snapshot: Boolean(inp.formation?.is_snapshot ?? summary?.is_snapshot),
    steps,
    picks: {
      candidate: top
        ? { slug: top.slug, title: top.title, score: top.score, precise: preciseScore(top), tier: top.tier, families: top.families, independent_signals: top.independent_signals }
        : null,
      person,
      meet: meetTop
        ? { person_id: meetTop.person.id, slug: meetTop.person.slug, name: meetTop.person.name, priority: meetTop.priority, brief_report_id: meetingBrief }
        : null,
      company: company ? { id: company.id, slug: company.slug, name: company.name, attention_gap: company.attention_gap } : null,
      brief,
    },
    counts: { formations: cards.length, people: inp.meet?.people.length ?? 0, signals: null },
  };
}
