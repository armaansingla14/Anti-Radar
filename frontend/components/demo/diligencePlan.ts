/**
 * The /demo/diligence tour plan: nine scenes that follow one company from the radar into technical
 * diligence and then audit the research system itself. Pure functions over API payloads (React-free,
 * relative imports; tested by tests/diligence-demo.test.mts). Nothing here hard-codes a name or a number:
 * every pick and caption is derived from whatever the backend holds, and each scene degrades to a list page
 * when its entity is missing.
 */
import { claimLabel, groupEvidence, ratingText, splitReason, stateLabel, statusCounts, statusLabel, testabilityLabel } from "../diligence/logic.ts";
import { caseResult, failureDisplay, formatMetric } from "../agents/logic.ts";
import { dayLabel, signed } from "../changes/logic.ts";
import type { SinceSnapshot } from "../../lib/types.ts";
import type { ChangesReport, ScoreChange } from "../../lib/types-changes.ts";
import type {
  DiligenceClaim,
  DiligenceIndex,
  DiligenceWorkspace,
  FounderQuestion,
  UnderwritingCategory,
  WorkspaceSummary,
} from "../../lib/types-diligence.ts";
import type { AgentsOverview, CaseOut, FailureOut } from "../../lib/types-agents.ts";
import type { DemoStep, TourPlan } from "./plan.ts";

export const DILIGENCE_SCENE_COUNT = 9;

export type DiligenceSceneKey =
  | "title"
  | "radar"
  | "workspace"
  | "ledger"
  | "claim"
  | "test-plan"
  | "underwriting"
  | "questions"
  | "agents"
  | "final";

export interface DiligenceInputs {
  index: DiligenceIndex | null;
  /** GET /diligence/{slug} for the picked workspace. */
  workspace: DiligenceWorkspace | null;
  /** The company's name, Anti Score and since-snapshot change (GET /companies/{slug}). */
  company: { name: string; score: number | null; since_snapshot?: SinceSnapshot | null } | null;
  changes: ChangesReport | null;
  agents: AgentsOverview | null;
  failures: FailureOut[];
  cases: CaseOut[];
}

export interface DiligencePlan extends TourPlan {
  picks: {
    workspace: { slug: string; name: string; status: string; report_id: string | null } | null;
    /** Where the company shows up in Radar for scene 1. */
    radar: { url: string; list: "weakened" | "accelerated" | null; score: number | null; delta: number | null } | null;
    claim: { ordinal: number; claim_type: string; status: string; testability: string; rule: string | null } | null;
    risk: string | null;
    evidence: string | null;
    question: { index: number; claim_ordinals: number[] } | null;
    failure: { number: number; agent: string; origin: string; regression_case: string | null } | null;
    case: { id: string; result: string } | null;
  };
  counts: { claims: number; questions: number; failures: number; pinned: number };
}

const TESTABILITY_RANK: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2, NOT_TESTABLE: 3 };
const rankOf = (t: string) => TESTABILITY_RANK[(t ?? "").toUpperCase()] ?? 4;

/** The most recently finished workspace with a run, real companies before demo ones. */
export function pickWorkspace(index: DiligenceIndex | null): WorkspaceSummary | null {
  const all = (index?.workspaces ?? []).filter((w) => w.run);
  if (!all.length) return null;
  const real = all.filter((w) => !w.company.is_demo);
  const pool = real.length ? real : all;
  const when = (w: WorkspaceSummary) => w.finished_at ?? w.run?.finished_at ?? "";
  return [...pool].sort((a, b) => when(b).localeCompare(when(a)) || a.company.slug.localeCompare(b.company.slug))[0];
}

/**
 * Scene 4: the most testable PERFORMANCE claim that is still UNVERIFIED (the interesting case — a
 * reproducible figure nobody has reproduced); else the first HIGH-testability claim; else the first claim.
 */
export function pickClaim(claims: DiligenceClaim[]): DiligenceClaim | null {
  if (!claims.length) return null;
  const byOrd = [...claims].sort((a, b) => a.ordinal - b.ordinal);
  const perf = byOrd
    .filter((c) => c.claim_type === "PERFORMANCE" && c.status === "UNVERIFIED")
    .sort((a, b) => rankOf(a.testability) - rankOf(b.testability) || a.ordinal - b.ordinal);
  return perf[0] ?? byOrd.find((c) => (c.testability ?? "").toUpperCase() === "HIGH") ?? byOrd[0];
}

/** Scene 6 risk card: the highest risk-scale rating (HIGH, then MODERATE, then any risk card). */
export function pickRiskCard(uw: UnderwritingCategory[]): UnderwritingCategory | null {
  const risk = uw.filter((u) => u.scale === "risk");
  const order = ["HIGH", "MODERATE", "LOW", "UNKNOWN"];
  return [...risk].sort((a, b) => order.indexOf(a.rating) - order.indexOf(b.rating))[0] ?? null;
}

/** Scene 6 evidence card: the first evidence-scale card (the workspace orders the most material first). */
export function pickEvidenceCard(uw: UnderwritingCategory[]): UnderwritingCategory | null {
  return uw.find((u) => u.scale === "evidence") ?? null;
}

/** Scene 7: the critical question (the executive summary's), else the highest-priority question. */
export function pickQuestion(questions: FounderQuestion[], critical: string | null | undefined): number | null {
  if (!questions.length) return null;
  const i = critical ? questions.findIndex((q) => q.question === critical) : -1;
  if (i >= 0) return i;
  let best = 0;
  questions.forEach((q, j) => {
    if ((q.priority ?? 0) > (questions[best].priority ?? 0)) best = j;
  });
  return best;
}

/**
 * Scene 8 failure: one the automatic evaluator caught in the research system's own guard (an injection-guard
 * false positive), pinned by a regression case. Falls back to any evaluator-found failure with a case, then any
 * injection-guard failure, then the newest fixed failure with a case.
 */
export function pickFailure(failures: FailureOut[]): FailureOut | null {
  const guard = (f: FailureOut) => /INJECTION/i.test(f.agent);
  const pinned = (f: FailureOut) => Boolean(f.regression_case || f.regression_test);
  const newest = (xs: FailureOut[]) => [...xs].sort((a, b) => b.number - a.number)[0] ?? null;
  return (
    newest(failures.filter((f) => guard(f) && f.origin === "evaluator" && pinned(f))) ??
    newest(failures.filter((f) => f.origin === "evaluator" && pinned(f))) ??
    newest(failures.filter((f) => guard(f) && pinned(f))) ??
    newest(failures.filter((f) => f.status === "FIXED" && pinned(f))) ??
    null
  );
}

/** Where the company appears in Radar: its row on /changes (weakened or accelerated), else its company page. */
export function pickRadarSpot(slug: string, changes: ChangesReport | null): { list: "weakened" | "accelerated"; change: ScoreChange } | null {
  const sc = changes?.score_changes;
  if (!sc?.comparable) return null;
  const w = sc.weakened.items.find((c) => c.slug === slug);
  if (w) return { list: "weakened", change: w };
  const a = sc.accelerated.items.find((c) => c.slug === slug);
  if (a) return { list: "accelerated", change: a };
  return null;
}

const pct = (v: number | null | undefined) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const clip = (s: string, n = 90) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

export function buildDiligencePlan(inp: DiligenceInputs): DiligencePlan {
  const ws = inp.workspace?.run ? inp.workspace : null;
  const summary = pickWorkspace(inp.index);
  const slug = ws?.company.slug ?? summary?.company.slug ?? null;
  const name = inp.company?.name ?? ws?.company.name ?? summary?.company.name ?? null;
  const wsUrl = slug ? `/diligence/${encodeURIComponent(slug)}` : "/diligence";
  const claim = ws ? pickClaim(ws.claims) : null;
  const claimUrl = slug && claim ? `${wsUrl}/claims/${claim.ordinal}` : null;
  const risk = ws ? pickRiskCard(ws.underwriting) : null;
  const evid = ws ? pickEvidenceCard(ws.underwriting) : null;
  const qIndex = ws ? pickQuestion(ws.questions, ws.executive_summary?.critical_question) : null;
  const question = ws && qIndex !== null ? ws.questions[qIndex] : null;
  const failure = pickFailure(inp.failures);
  const kase = failure?.regression_case ? inp.cases.find((c) => c.id === failure.regression_case) ?? null : null;
  const spot = slug ? pickRadarSpot(slug, inp.changes) : null;
  const since = inp.company?.since_snapshot;
  const score = inp.company?.score ?? spot?.change.current_score ?? null;
  const defs = ws?.definitions;

  const s = (step: Omit<DemoStep, "match" | "click" | "ready" | "focus"> & Partial<DemoStep>): DemoStep => ({
    match: step.url ? step.url.split("?")[0].split("#")[0] : null,
    click: null,
    ready: null,
    focus: null,
    ...step,
  });

  const steps: DemoStep[] = [s({ scene: 0, scene_key: "title", title: "Technical diligence", caption: "Radar finds it. Diligence tests it. People decide.", url: null })];

  // 1 — the company as Radar shows it (a weakened company is shown as readily as a rising one).
  // Scoped to its list: the same company can also appear under Momentum acceleration further down.
  const row = slug && spot ? `#${spot.list} ~ * [data-demo="score-row"][data-demo-slug="${slug}"]` : null;
  let radar: DiligencePlan["picks"]["radar"] = null;
  if (slug && spot && row) {
    const d = spot.change.delta;
    const base = dayLabel(inp.changes?.base_day) ?? "the last snapshot";
    radar = { url: "/changes", list: spot.list, score: spot.change.current_score, delta: d };
    steps.push(
      s({
        scene: 1,
        scene_key: "radar",
        title: "On the radar",
        caption: `${name} — Anti Score ${spot.change.current_score}, ${signed(d, 0)} since ${base}: ${spot.list}. Radar shows a decline as plainly as a gain.`,
        url: "/changes",
        ready: row,
        focus: row,
        focus_offset: 220,
      }),
    );
  } else if (slug) {
    const comparable = since?.comparable && typeof since.delta === "number";
    radar = { url: `/companies/${encodeURIComponent(slug)}`, list: null, score, delta: comparable ? since!.delta : null };
    steps.push(
      s({
        scene: 1,
        scene_key: "radar",
        title: "On the radar",
        caption: `${name} — Anti Score ${score ?? "—"}${comparable ? `, ${signed(since!.delta!, 0)} since ${dayLabel(since!.base_day) ?? "the last snapshot"}` : ""}`,
        url: `/companies/${encodeURIComponent(slug)}`,
        ready: '[aria-label="Diligence status"]',
        focus: '[aria-label="Diligence status"]',
        focus_offset: 260,
      }),
    );
  } else {
    steps.push(s({ scene: 1, scene_key: "radar", title: "On the radar", caption: "No company has a diligence workspace yet.", url: "/diligence", ready: "h1" }));
  }

  // 2 — open diligence (clicking the row's own link when it is on screen).
  if (ws && slug) {
    const pending = ws.status.pending_review ?? [];
    const why = pending.length ? `${plural(pending.length, "pending review")}: ${pending.map((p) => p.label).join("; ")}` : ws.status.last_error ? `last run failed: ${ws.status.last_error}` : null;
    steps.push(
      s({
        scene: 2,
        scene_key: "workspace",
        title: "Open diligence",
        caption: `Diligence status ${stateLabel(ws.status.status)}${why ? ` — ${why}` : ""} · coverage ${pct(ws.header.evidence_coverage)} · ${ws.header.sources_analyzed} sources analyzed`,
        url: wsUrl,
        click: radar?.list ? `${row} [data-demo="open-diligence"]` : null,
        ready: '[data-demo="diligence-header"]',
        focus: '[data-demo="diligence-header"]',
        focus_offset: 120,
      }),
    );
    // 3 — the claim ledger, counted by status.
    const es = ws.executive_summary;
    const counts = statusCounts(es?.counts ?? ws.status.counts);
    steps.push(
      s({
        scene: 3,
        scene_key: "ledger",
        title: "Claim ledger",
        caption: `${plural(es?.material_claims ?? ws.claims.length, "material claim")}: ${counts.map((c) => `${c.count} ${c.label.toLowerCase()}`).join(" · ")}`,
        url: wsUrl,
        ready: "#summary",
        focus: "#summary",
        focus_offset: 30,
      }),
    );
  } else {
    steps.push(
      s({ scene: 2, scene_key: "workspace", title: "Open diligence", caption: "Diligence has not been run yet.", url: wsUrl, ready: "h1" }),
      s({ scene: 3, scene_key: "ledger", title: "Claim ledger", caption: "No claims extracted yet.", url: wsUrl, ready: "h1" }),
    );
  }

  // 4–5 — one testable claim: its evidence by publisher, then its (generated, never executed) test plan.
  if (claim && claimUrl) {
    const g = groupEvidence(claim);
    const reason = splitReason(claim.status_reason, claim.rule);
    steps.push(
      s({
        scene: 4,
        scene_key: "claim",
        title: "A claim and its evidence",
        caption: `${claimLabel(claim.ordinal)}, ${claim.claim_type.replace(/_/g, " ").toLowerCase()}, testability ${testabilityLabel(claim.testability).toLowerCase()} — evidence: company ${g.company.length} · public artifact ${g.artifact.length} · third party ${g.thirdParty.length} · ${reason.id ? `${reason.id}: ` : ""}${reason.text} → ${statusLabel(claim.status, defs?.status_labels)}`,
        url: claimUrl,
        ready: "#evidence",
        focus: "#evidence",
        focus_offset: 30,
        hold_top: 2400,
      }),
    );
    const plan = claim.test_plan;
    const executed = plan?.execution?.status && plan.execution.status !== "not_executed";
    steps.push(
      s({
        scene: 5,
        scene_key: "test-plan",
        title: "Test plan",
        caption: plan
          ? `${executed ? plan.execution!.status!.replace(/_/g, " ") : "Generated, not run"} — baseline ${plan.baseline?.name ?? "not named"}, ${plural(plan.metrics?.length ?? 0, "metric")}, ${plural(plan.procedure?.length ?? 0, "step")}, explicit pass criteria; untrusted code is never executed on the radar host`
          : "No test plan: this claim cannot be tested from public artifacts.",
        url: claimUrl,
        ready: plan ? '[data-demo="test-plan"]' : "#assessment",
        focus: plan ? '[data-demo="test-plan"]' : "#assessment",
        focus_offset: 30,
      }),
    );
  } else {
    steps.push(
      s({ scene: 4, scene_key: "claim", title: "A claim and its evidence", caption: "No claim to inspect.", url: wsUrl, ready: "h1" }),
      s({ scene: 5, scene_key: "test-plan", title: "Test plan", caption: "No claim to inspect.", url: wsUrl, ready: "h1" }),
    );
  }

  // 6 — technical underwriting: a risk-scale card, then an evidence-scale card (two scales, never one score).
  if (ws && (risk || evid)) {
    for (const u of [risk, evid].filter((x): x is UnderwritingCategory => Boolean(x))) {
      steps.push(
        s({
          scene: 6,
          scene_key: "underwriting",
          title: "Technical underwriting",
          caption: `${u.title} · ${ratingText(u)} — ${clip(u.headline, 150)}`,
          url: wsUrl,
          ready: `#uw-${u.key}`,
          focus: `#uw-${u.key}`,
          focus_offset: 30,
        }),
      );
    }
  } else {
    steps.push(s({ scene: 6, scene_key: "underwriting", title: "Technical underwriting", caption: "No underwriting in this run.", url: wsUrl, ready: "h1" }));
  }

  // 7 — founder questions, each with why it is asked.
  if (ws && question && qIndex !== null) {
    const from = question.claim_ordinals.length ? ` (from ${question.claim_ordinals.map(claimLabel).join(", ").toLowerCase()})` : "";
    steps.push(
      s({
        scene: 7,
        scene_key: "questions",
        title: "Questions for the founders",
        caption: `${plural(ws.questions.length, "question")}${from} — why: ${question.reason}`,
        url: wsUrl,
        ready: `#question-${qIndex + 1}`,
        focus: `#question-${qIndex + 1}`,
        focus_offset: 90,
      }),
    );
  } else {
    steps.push(s({ scene: 7, scene_key: "questions", title: "Questions for the founders", caption: "No founder questions in this run.", url: wsUrl, ready: "h1" }));
  }

  // 8 — the research system audits itself: agent metrics → a real failure it caught → the case that pins it.
  const analyst = inp.agents?.agents.find((a) => /DILIGENCE/i.test(a.name)) ?? null;
  const metric = (k: string, kind: "rate" | "number") => formatMetric(analyst?.metrics[k], kind).text;
  steps.push(
    s({
      scene: 8,
      scene_key: "agents",
      title: "Can we trust the agents?",
      caption: inp.agents
        ? `${plural(inp.agents.agents.length, "agent")} evaluated${analyst ? ` · ${analyst.name}: ${plural(analyst.runs, "run")}, citation validity ${metric("citation_validity", "rate")}, unsupported claims ${metric("unsupported_claim_rate", "rate")}` : ""} · regression ${inp.agents.regression.passing}/${inp.agents.regression.cases_total} passing`
        : "Agent evaluation unavailable.",
      url: "/agents",
      ready: "#agents",
      focus: "#agents",
      focus_offset: 30,
    }),
  );
  if (failure) {
    const failureUrl = `/agents/failures/${String(failure.number).padStart(4, "0")}`;
    steps.push(
      s({
        scene: 8,
        scene_key: "agents",
        title: "A failure it caught",
        caption: `${failureDisplay(failure)} · ${failure.agent.toLowerCase()} · found by the ${failure.origin === "evaluator" ? "automatic evaluator" : failure.origin} — ${clip(failure.claim_text, 80)} · ${failure.status.toLowerCase()}`,
        url: failureUrl,
        ready: '[data-demo="failure"]',
        focus: '[data-demo="failure-evidence"]',
        focus_offset: 200,
        hold_top: 2200,
      }),
    );
    if (kase) {
      steps.push(
        s({
          scene: 8,
          scene_key: "agents",
          title: "Pinned by a regression case",
          caption: `${kase.id} — ${kase.title} · latest run: ${caseResult(kase)}`,
          url: `/agents/cases#case-${kase.id}`,
          match: "/agents/cases",
          click: '[data-demo="regression-case"]',
          ready: `#case-${kase.id}`,
          focus: `#case-${kase.id}`,
          focus_offset: 200,
        }),
      );
    }
  }

  steps.push(s({ scene: 9, scene_key: "final", title: "Radar finds it. Diligence tests it. People decide.", caption: "", url: null }));

  return {
    as_of: ws?.header.date ?? summary?.run?.collected_at?.slice(0, 10) ?? null,
    is_snapshot: Boolean(inp.changes?.is_snapshot),
    steps,
    picks: {
      workspace: slug && name ? { slug, name, status: ws?.status.status ?? summary?.status ?? "NOT_RUN", report_id: ws?.report_id ?? null } : null,
      radar,
      claim: claim ? { ordinal: claim.ordinal, claim_type: claim.claim_type, status: claim.status, testability: claim.testability, rule: claim.rule || null } : null,
      risk: risk?.key ?? null,
      evidence: evid?.key ?? null,
      question: question && qIndex !== null ? { index: qIndex, claim_ordinals: question.claim_ordinals } : null,
      failure: failure ? { number: failure.number, agent: failure.agent, origin: failure.origin, regression_case: failure.regression_case } : null,
      case: kase ? { id: kase.id, result: caseResult(kase) } : null,
    },
    counts: {
      claims: ws?.claims.length ?? 0,
      questions: ws?.questions.length ?? 0,
      failures: inp.failures.length,
      pinned: inp.failures.filter((f) => f.regression_case || f.regression_test).length,
    },
  };
}
