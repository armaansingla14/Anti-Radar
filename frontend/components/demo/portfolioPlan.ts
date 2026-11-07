/**
 * The /demo/portfolio tour plan: nine scenes from Portfolio Pulse through one company, one material event, its
 * competitive map, the diligence handoff, the portfolio brief and the agents' own audit trail. Pure functions
 * over API payloads (React-free, relative imports; tested by tests/portfolio-demo.test.mts). Nothing here
 * hard-codes a name or a number: every pick and caption comes from the payloads, and each scene degrades to a
 * list page when its entity is missing.
 */
import { formatMetric } from "../agents/logic.ts";
import { claimLabel, statusLabel } from "../diligence/logic.ts";
import { actionLabel, allComps, compTypeLabel, funnelLine, subtypeLabel, thingsWorthKnowing } from "../portfolio/logic.ts";
import type { DiligenceClaim, DiligenceWorkspace } from "../../lib/types-diligence.ts";
import type { AgentsOverview, FailureOut } from "../../lib/types-agents.ts";
import type { AttentionCard, Comp, CompsMap, EventDetail, PEvent, PortfolioBrief, PortfolioCompany, Pulse } from "../../lib/types-portfolio.ts";
import type { DemoStep, TourPlan } from "./plan.ts";

export const PORTFOLIO_SCENE_COUNT = 9;
export const PORTFOLIO_AGENT = "PORTFOLIO ANALYST";

export interface PortfolioInputs {
  pulse: Pulse | null;
  /** GET /portfolio/{slug} for the company picked for scenes 3–5. */
  company: PortfolioCompany | null;
  /** GET /portfolio/events/{id} for the event picked for scene 4. */
  event: EventDetail | null;
  comps: CompsMap[] | null;
  /** The RUN DILIGENCE handoff (scene 6): the company's view, its triggering event and the V3 workspace. */
  handoff: { company: PortfolioCompany | null; event: EventDetail | null; workspace: DiligenceWorkspace | null };
  brief: PortfolioBrief | null;
  agents: AgentsOverview | null;
  /** Failures of the PORTFOLIO ANALYST agent (GET /agents/failures?agent=…). */
  failures: FailureOut[];
  /** The backend serves the verified snapshot (GET /home summary.is_snapshot). */
  isSnapshot?: boolean;
}

export interface PortfolioPlan extends TourPlan {
  picks: {
    company: { slug: string; name: string; action: string } | null;
    event: { id: string; title: string; materiality: string; rule: string } | null;
    comp: { slug: string; name: string; comp_type: string } | null;
    handoff: { slug: string; name: string; event_id: string | null; claim: number | null; workspace: boolean } | null;
  };
  counts: { tracked: number; raw_changes: number; material: number; failures: number; pinned: number };
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
/** Shorten at a word boundary ("…" replaces the rest; trailing punctuation dropped). */
export function clip(s: string, n = 90): string {
  if (s.length <= n) return s;
  const cut = s.slice(0, n);
  const at = cut.lastIndexOf(" ");
  return `${(at > n * 0.6 ? cut.slice(0, at) : cut).replace(/[\s,;:.—-]+$/, "")}…`;
}
/** End a sentence: "…" and "." already end one. */
const end = (t: string) => (/[.…!?]$/.test(t) ? t : `${t}.`);
/** "3 material events … in 28 days, from 2 independent source families" → without the source-family clause. */
const whyShort = (why: string) => why.replace(/,? from \d+ independent source famil(?:y|ies)\.?$/i, "");
const enc = encodeURIComponent;

/**
 * Scene 3: the highest-ranked Needs Attention company with at least 3 material/watch events including a
 * competitor event (it shows both what the company did and what its competitors did); else the first card.
 */
export function pickCompany(cards: AttentionCard[] | null | undefined): AttentionCard | null {
  const list = cards ?? [];
  return (
    list.find((c) => c.what_changed.length >= 3 && c.what_changed.some((w) => w.relation !== "SELF") && c.material_events.length > 0) ??
    list.find((c) => c.what_changed.length >= 3 && c.what_changed.some((w) => w.relation !== "SELF")) ??
    list[0] ??
    null
  );
}

/** Scene 4: the card's first material event, else its first event to watch. */
export function pickEventId(card: AttentionCard | null): string | null {
  if (!card) return null;
  return card.material_events[0] ?? card.what_changed[0]?.event_id ?? null;
}

/**
 * Scene 5: the comp behind the picked event when the event is a comp's; else the comp of the company with the
 * most events in the window (DIRECT first).
 */
export function pickComp(map: CompsMap | null | undefined, eventSubject: string | null): Comp | null {
  const comps = allComps(map);
  if (!comps.length) return null;
  const subject = eventSubject ? comps.find((c) => c.company.slug === eventSubject) : undefined;
  if (subject) return subject;
  return [...comps].sort((a, b) => b.recent_events.length - a.recent_events.length || Number(b.comp_type === "DIRECT") - Number(a.comp_type === "DIRECT"))[0];
}

/** Scene 6: the Needs Attention card whose suggested action is RUN DILIGENCE. */
export function pickHandoffCard(cards: AttentionCard[] | null | undefined): AttentionCard | null {
  return (cards ?? []).find((c) => c.suggested_action === "RUN_DILIGENCE") ?? null;
}

/** The company's own event that prompted RUN DILIGENCE (a performance / benchmark claim), newest first. */
export function pickHandoffEvent(view: PortfolioCompany | null): PEvent | null {
  const own = view?.events ?? [];
  return own.find((e) => e.suggested_review === "RUN_DILIGENCE") ?? own.find((e) => e.performance_claim) ?? null;
}

/** The diligence claim the event states: same source URL, else the first PERFORMANCE claim. */
export function pickHandoffClaim(ws: DiligenceWorkspace | null, event: PEvent | null): DiligenceClaim | null {
  const claims = [...(ws?.claims ?? [])].sort((a, b) => a.ordinal - b.ordinal);
  if (!claims.length) return null;
  const urls = new Set(event?.source_urls ?? []);
  return claims.find((c) => c.source_url && urls.has(c.source_url)) ?? claims.find((c) => c.claim_type === "PERFORMANCE") ?? null;
}

/** Portfolio failures: how many there are and how many a regression case or test pins. */
export function failureCounts(failures: FailureOut[]): { total: number; pinned: number; documented: number } {
  return {
    total: failures.length,
    pinned: failures.filter((f) => f.regression_case || f.regression_test).length,
    documented: failures.filter((f) => f.origin === "documented").length,
  };
}

export function buildPortfolioPlan(inp: PortfolioInputs): PortfolioPlan {
  const p = inp.pulse;
  const cards = p?.needs_attention ?? [];
  const card = pickCompany(cards);
  const view = inp.company && card && inp.company.company.slug === card.company.slug ? inp.company : null;
  const slug = card?.company.slug ?? null;
  const name = card?.company.name ?? null;
  const ev = inp.event?.event ?? null;
  const map = slug ? (inp.comps ?? []).find((m) => m.company.slug === slug) ?? null : null;
  const comp = pickComp(map, ev && ev.company.slug !== slug ? ev.company.slug : null);
  const hCard = pickHandoffCard(cards);
  const hView = inp.handoff.company;
  const hEvent = inp.handoff.event?.event ?? pickHandoffEvent(hView);
  const ws = inp.handoff.workspace?.run ? inp.handoff.workspace : null;
  const claim = pickHandoffClaim(ws, hEvent);
  const fc = failureCounts(inp.failures);
  const counts = p?.counts ?? {};
  const funnel = p?.funnel ?? null;

  const s = (step: Omit<DemoStep, "match" | "click" | "ready" | "focus"> & Partial<DemoStep>): DemoStep => ({
    match: step.url ? step.url.split("?")[0].split("#")[0] : null,
    click: null,
    ready: null,
    focus: null,
    ...step,
  });

  const steps: DemoStep[] = [s({ scene: 0, scene_key: "title", title: "Portfolio Pulse", caption: "What changed after we cared?", url: null })];

  // 1 — Portfolio Pulse, with the real counts.
  steps.push(
    s({
      scene: 1,
      scene_key: "pulse",
      title: "Portfolio Pulse",
      caption: p
        ? `Radar answers who is becoming interesting. Portfolio Pulse answers what changed after you already care — ${plural(counts.need_attention ?? 0, "company", "companies")} need attention · ${plural(counts.material ?? 0, "material event")} · ${counts.watch ?? 0} to watch, across ${plural(p.monitoring.portfolio_companies, "portfolio company", "portfolio companies")}.`
        : "Portfolio Pulse is unavailable.",
      url: "/portfolio",
      ready: '[data-demo="portfolio-counts"]',
      focus: '[data-demo="portfolio-counts"]',
      focus_offset: 260,
    }),
  );

  // 2 — Needs Attention.
  steps.push(
    s({
      scene: 2,
      scene_key: "attention",
      title: "Needs attention",
      caption: cards.length
        ? `${plural(cards.length, "company", "companies")} need attention. First: ${cards[0].company.name} — ${clip(whyShort(cards[0].why_surfaced), 90)}. Suggested: ${actionLabel(cards[0].suggested_action)}.`
        : "No portfolio company needs attention in the window — a true result.",
      url: "/portfolio",
      ready: "#needs-attention",
      focus: "#needs-attention",
      focus_offset: 30,
    }),
  );

  // 3 — one company's portfolio view.
  if (slug && name) {
    const own = (view?.events ?? []).filter((e) => e.materiality !== "NOISE");
    const theirs = view?.competitor_events ?? [];
    const stage = view?.position.investment_stage ?? null;
    steps.push(
      s({
        scene: 3,
        scene_key: "company",
        title: name,
        caption: view
          ? `${name}${stage ? ` (${stage}, ${view.position.investment_year ?? "year unknown"} per antifund.com)` : ""}: ${plural(own.length, "own event")}, ${plural(theirs.length, "competitor event")}, ${plural(view.questions.length, "open question")} in the window. Suggested: ${actionLabel(card!.suggested_action)}.`
          : `${name}: ${card!.why_surfaced}.`,
        url: `/portfolio/${enc(slug)}`,
        click: `#attention-${slug} header a[href="/portfolio/${slug}"]`,
        ready: '[data-demo="portfolio-header"]',
        focus: theirs.length ? "#competitive-changes" : "#material-events",
        focus_offset: 30,
        hold_top: 3200,
      }),
    );
  } else {
    steps.push(s({ scene: 3, scene_key: "company", title: "A portfolio company", caption: "No company to open.", url: "/portfolio", ready: "h1" }));
  }

  // 4 — one material event: source, rule, why it matters, questions.
  if (ev) {
    const d = inp.event!;
    const src = ev.sources[0];
    const reaches = (ev.affects ?? []).filter((a) => a.relation !== "SELF").map((a) => `${a.company_name} (${(a.comp_type ?? a.relation).toLowerCase()})`);
    steps.push(
      s({
        scene: 4,
        scene_key: "event",
        title: "A material event",
        caption: `${ev.company.name}: “${clip(ev.title, 80)}” — ${ev.materiality} by ${ev.materiality_rule}: ${ev.materiality_reason}. Source: ${src?.publisher === "company" ? "the company's own post" : (src?.publisher ?? "public source")}.${reaches.length ? ` Reaches ${reaches.join(", ")}.` : ""}`,
        url: `/portfolio/events/${enc(ev.id)}`,
        ready: "#sources",
        focus: "#sources",
        focus_offset: 320,
      }),
      s({
        scene: 4,
        scene_key: "event",
        title: "Why it matters",
        caption: `Why it matters (inference): ${ev.why_it_matters} ${plural(d.questions.length, "question")} raised · suggested: ${actionLabel(d.suggested_action)} · decision trace: system steps logged, human review awaited.`,
        url: `/portfolio/events/${enc(ev.id)}`,
        ready: "#why",
        focus: "#why",
        focus_offset: 30,
      }),
    );
  } else {
    steps.push(s({ scene: 4, scene_key: "event", title: "A material event", caption: "No material event in the window.", url: "/portfolio/events", ready: "h1" }));
  }

  // 5 — the competitive map: the comp behind that event, why it is comparable, and its events reaching the company.
  if (slug && comp) {
    const evs = comp.recent_events;
    steps.push(
      s({
        scene: 5,
        scene_key: "comps",
        title: "Competitive map",
        caption: `${comp.company.name} is a ${compTypeLabel(comp.comp_type).toLowerCase()} comp of ${name} (${comp.confidence.toLowerCase()} confidence, sourced inference): ${end(clip(comp.why_comparable, 70))}${evs.length ? ` ${plural(evs.length, "event")} in the window, ${evs.filter((e) => e.materiality === "MATERIAL").length} material.` : ""}`,
        url: `/portfolio/comps#comps-${slug}`,
        match: "/portfolio/comps",
        ready: `#comps-${slug} [data-comp="${comp.company.slug}"]`,
        focus: `#comps-${slug} [data-comp="${comp.company.slug}"]`,
        focus_offset: 220,
      }),
    );
  } else {
    steps.push(s({ scene: 5, scene_key: "comps", title: "Competitive map", caption: "No comps configured.", url: "/portfolio/comps", ready: "h1" }));
  }

  // 6 — RUN DILIGENCE: the card's action → its event → the existing V3 workspace (never a live run in the tour).
  let handoff: PortfolioPlan["picks"]["handoff"] = null;
  if (hCard && hEvent) {
    const hSlug = hCard.company.slug;
    handoff = { slug: hSlug, name: hCard.company.name, event_id: hEvent.id, claim: claim?.ordinal ?? null, workspace: Boolean(ws) };
    steps.push(
      s({
        scene: 6,
        scene_key: "diligence",
        title: "Run diligence",
        caption: `${hCard.company.name}'s card suggests RUN DILIGENCE: “${clip(hEvent.title, 70)}” is a ${subtypeLabel(hEvent.event_subtype)} — ${hEvent.materiality} by ${hEvent.materiality_rule}. A claim to check against public evidence, not to repeat.`,
        url: `/portfolio/events/${enc(hEvent.id)}`,
        ready: "#action",
        focus: "#action",
        focus_offset: 200,
      }),
    );
    if (ws) {
      const wsUrl = `/diligence/${enc(hSlug)}`;
      steps.push(
        s({
          scene: 6,
          scene_key: "diligence",
          title: "The diligence workspace",
          caption: `Open diligence → the existing workspace from its last run (${ws.header.date ?? "date unknown"}; the tour opens it, it does not run diligence): ${plural(ws.claims.length, "material claim")}, ${ws.header.sources_analyzed} sources analyzed.`,
          url: wsUrl,
          click: '#action [data-demo="open-diligence"]',
          ready: '[data-demo="diligence-header"]',
          focus: '[data-demo="diligence-header"]',
          focus_offset: 120,
        }),
      );
      if (claim) {
        steps.push(
          s({
            scene: 6,
            scene_key: "diligence",
            title: "The claim and its test plan",
            caption: `${claimLabel(claim.ordinal)}: “${clip(claim.text, 70)}” — ${statusLabel(claim.status, ws.definitions?.status_labels)}${claim.test_plan ? "; test plan generated, not run" : ""}.`,
            url: `${wsUrl}/claims/${claim.ordinal}`,
            ready: claim.test_plan ? '[data-demo="test-plan"]' : "#evidence",
            focus: claim.test_plan ? '[data-demo="test-plan"]' : "#evidence",
            focus_offset: 30,
            hold_top: 2600,
          }),
        );
      }
    }
  } else {
    steps.push(
      s({ scene: 6, scene_key: "diligence", title: "Run diligence", caption: "No company's suggested action is RUN DILIGENCE in this window.", url: "/diligence", ready: "h1" }),
    );
  }

  // 7 — the portfolio morning brief and the noise funnel.
  const b = inp.brief?.body ?? null;
  steps.push(
    s({
      scene: 7,
      scene_key: "brief",
      title: "Portfolio brief",
      caption: b
        ? `The stored morning brief (${b.date}): ${thingsWorthKnowing(b.five_things.length).toLowerCase()}, ${plural(b.needs_attention.length, "company", "companies")} needing attention, every line sourced.`
        : "No portfolio brief stored yet.",
      url: "/portfolio/brief",
      ready: b ? '[data-demo="portfolio-brief"]' : "h1",
      focus: b ? '[data-demo="portfolio-brief"]' : null,
      focus_offset: 30,
    }),
  );
  if (b && funnel?.available) {
    steps.push(
      s({
        scene: 7,
        scene_key: "brief",
        title: "Noise filtered",
        caption: `${funnelLine(funnel)}. Noise is counted and filtered by rule, not hidden.`,
        url: "/portfolio/brief",
        ready: "#r-sec-noise_filtered",
        focus: "#r-sec-noise_filtered",
        focus_offset: 160,
      }),
    );
  }

  // 8 — the agent that wrote the brief is evaluated like every other agent; its real failures are pinned.
  const analyst = inp.agents?.agents.find((a) => a.name === PORTFOLIO_AGENT) ?? null;
  const metric = (k: string, kind: "rate" | "number") => formatMetric(analyst?.metrics[k], kind).text;
  steps.push(
    s({
      scene: 8,
      scene_key: "agents",
      title: "Evaluated like every agent",
      caption: analyst
        ? `${PORTFOLIO_AGENT} is evaluated like every other agent: ${plural(analyst.runs, "run")}, structured validity ${metric("structured_validity_rate", "rate")}, citation validity ${metric("citation_validity", "rate")} · regression ${inp.agents!.regression.passing}/${inp.agents!.regression.cases_total} passing.`
        : "Agent evaluation unavailable.",
      url: "/agents",
      ready: analyst ? `[data-demo-agent="${PORTFOLIO_AGENT}"]` : "#agents",
      focus: analyst ? `[data-demo-agent="${PORTFOLIO_AGENT}"]` : "#agents",
      focus_offset: 260,
    }),
  );
  if (fc.total > 0) {
    const newest = [...inp.failures].sort((a, b2) => b2.number - a.number)[0];
    steps.push(
      s({
        scene: 8,
        scene_key: "agents",
        title: "Its failures, pinned",
        caption: `${plural(fc.total, "real portfolio failure")} found${fc.documented === fc.total ? " by hand audit" : ""}, ${fc.pinned === fc.total ? "each" : `${fc.pinned}`} pinned by a regression test — newest: ${clip(newest.claim_text, 60)}`,
        url: `/agents/failures?agent=${enc(PORTFOLIO_AGENT)}`,
        ready: '[data-demo="failure-ledger"]',
        focus: '[data-demo="failure-ledger"]',
        focus_offset: 200,
      }),
    );
  }

  steps.push(s({ scene: 9, scene_key: "final", title: "Discover. Diligence. Monitor. Decide.", caption: "", url: null }));

  return {
    as_of: p?.monitoring.data_as_of ?? p?.day ?? null,
    is_snapshot: Boolean(inp.isSnapshot),
    steps,
    picks: {
      company: slug && name ? { slug, name, action: card!.suggested_action } : null,
      event: ev ? { id: ev.id, title: ev.title, materiality: ev.materiality, rule: ev.materiality_rule } : null,
      comp: comp ? { slug: comp.company.slug, name: comp.company.name, comp_type: comp.comp_type } : null,
      handoff,
    },
    counts: {
      tracked: p?.monitoring.tracked ?? 0,
      raw_changes: funnel?.available ? funnel.raw_changes : 0,
      material: counts.material ?? 0,
      failures: fc.total,
      pinned: fc.pinned,
    },
  };
}
