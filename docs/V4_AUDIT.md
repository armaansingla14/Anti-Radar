# V4 audit: Portfolio Pulse

V3 answers *who is becoming interesting* (Radar) and *what should we believe* (Diligence).

V4 adds **PORTFOLIO**: *what changed after we cared, and what deserves attention now?* It runs over the publicly disclosed Anti Fund portfolio, on the same Company entities, with the same provenance, review and agent-evaluation machinery. This audit lists what exists, what V4 reuses, what it adds, and where public data runs out.

## 1. What exists (V3)

| Area | Where | Reused for V4 |
|---|---|---|
| Company identity | `radar/models/core.py` `Company` (slug, website, domain, github_org, stage, thesis_tags, kind, status, attrs, is_demo) | The **only** company record. Portfolio data hangs off it through a position row, never a parallel profile. |
| Signals | `Signal` (signal_type, source_type, source_url, event_at, detected_at, confidence, epistemic, dedup_key); providers in `radar/providers/*`; ingest in `pipeline/ingest.py` | Raw changes: releases, repo creation, star/commit acceleration, papers, HN stories, hiring, formation language, affiliation changes |
| Hiring | `Job` (role_class, posted_at, first_seen_at, closed_at); `providers/ats.py` (Greenhouse, Lever, Ashby); `providers/roles.py classify_role`; `detectors/hiring.py hiring_summary` | Specialized hiring clusters, new role classes, hiring acceleration (always labeled as a public hiring signal, never as headcount) |
| Website | `WebPage` (page_kind home/about/careers/blog/news/docs/changelog, content_hash, last_changed_at) via `providers/website.py` | Page-change detection. The newsroom, blog and changelog pages are already fetched; V4 adds dated-item extraction from them. |
| Metrics | `MetricSnapshot` (entity, metric, day, value, source); `CompanyMetric` | The one metric table. V4 adds source_url, confidence and a proxy label. |
| History | Snapshot chain `demo/seed.py snapshot_chain` (base Oct 20, update Nov 5); `ScoreSnapshot`; `services/changes.py` (`material_delta`, `compare`, `company_since`) | Observed deltas between captures; comparable-window discipline |
| Freshness | `services/freshness.py` (`data_age` per source family, `provider_freshness`); `services/status.py` (healthy/degraded/stale per provider cadence) | Portfolio monitoring status: tracked / fresh / stale, provider health, X unavailable |
| Morning brief | `services/brief.py`, `ops.persist_morning_brief` (one stored `ResearchReport(kind=morning_brief)` per day), archive routes | The pattern for the stored daily Portfolio brief |
| Reports | `ResearchReport` (kind, body, markdown, sources S-ids, generator, trace, cost) | New kinds `portfolio_review` (change memo) and `portfolio_brief` |
| Relationships | `Relationship` (WORKED_WITH … CONTRIBUTED_TO, VERIFIED/INFERRED, evidence) | Team context. Comps get a dedicated sourced table, because "comparable" is a typed, cited judgement, not a graph edge. |
| Watchlist | `Watchlist`, `WatchlistItem`, `services/watchlist.py _changes` | The closest V3 "pulse" (per-item change lines). V4 supersedes it for portfolio companies and leaves it in place. |
| Materiality-like rules | `services/changes.MATERIAL = 5.0` score threshold; noise filters `services/meet.is_noise_repo_signal`, `providers/github.NOISE_NAME_RE`; detectors `detectors/strange.py` (hiring_pivot, quiet_specialized_hiring, code_and_jobs) | The shape of the materiality rules (documented thresholds, reasons) and reuse of the noise filters |
| Review | `ReviewDecision` (target_type, target_id, decision, note, reviewer), validated in `diligence/workspace.py` | Portfolio reviews (MARK REVIEWED / NEEDS FOLLOW-UP / MONITOR / NO ACTION) as new target types |
| Diligence | `diligence/run.py run_diligence`, `POST /api/diligence/{slug}/run`, workspaces, captures | The RUN DILIGENCE handoff. No second implementation. |
| Agent evaluation | `evals/taxonomy.AGENTS`, `evals/checks.check_report`, `evals/runner.register_task`, `evals/failures.DOCUMENTED`, `tests/evals/cases/*.yaml` | PORTFOLIO ANALYST is registered and evaluated like every other agent; confirmed failures become regression cases |
| Safety | SSRF-guarded HTTP client, robots.txt, size caps, `llm/guard` injection detection, external text as untrusted data | Every new collector goes through the same client |
| API / UI | Auto-registered `api/routes/*.py`; `_refuse_on_snapshot`; frontend module pattern (`logic.ts` + node tests, `canWrite` gating, `CompanyTabs`, `TourConfig` demo tours, `export-snapshot.mjs`, `verify_public_demo.mjs`) | Portfolio API and pages follow the same patterns |

## 2. Portfolio companies already present

There is no portfolio concept in the V3 data: no flag, no list, no config. The source of truth is the Anti Fund website, https://antifund.com/, checked Nov 6, 2026. It lists 53 companies, with a stage label and year for each.

Of those 53, **only Physical Intelligence** exists as a Company entity in the Oct 20 / Nov 5 snapshots.

Several others are named in configuration, but only as talent sources or affiliation tiers, not as entities:
- OpenAI, Anduril, SpaceX and Cognition are in `config/anti_fund_thesis.yaml` tier_1 and `config/talent_sources.yaml`.
- Helion is in tier_2.

V4 adds the monitored portfolio companies and their comps through the normal providers and entity resolution, so every record is created the same way as any other Radar company, and none is created twice.

## 3. What V4 adds

**Configuration.** `config/anti_fund_portfolio.yaml` holds the publicly disclosed technical portfolio companies. Each entry has the public source, stage and year exactly as stated, sector, thesis tags, status, and verified identifiers only. Each company also has its comps, each with a type, the reason it is comparable, a source URL and a confidence.

**Schema.** Migration 0008 adds these tables:

| Table | Purpose |
|---|---|
| `portfolio_positions` | Investment metadata linked to `companies.id`: public fields, plus a `private_fields` JSON that **must be empty in public mode** |
| `material_events` | Typed events (9 types, with subtypes), materiality MATERIAL / WATCH / NOISE with the rule that decided it, sources, epistemic, why it matters, suggested review, dedup key |
| `event_impacts` | An event's effect on a portfolio company (SELF / COMPETITOR / ADJACENT), with reason and confidence |
| `comp_relationships` | Company → comp company, with type, why comparable, source, confidence, epistemic INFERENCE |
| `portfolio_questions` | Persistent "what changed since we last looked" questions, each created from an event |
| `portfolio_decisions` | The decision log, tracing EVENT → REVIEW → QUESTION → ACTION. System steps and real human steps only. |

It also changes two existing tables:
- `metric_snapshots` gains `source_url`, `confidence` and `proxy_label`.
- `review_decisions` accepts the new target types and portfolio decisions (validation in code).

**Engine.** `radar/portfolio/` is deterministic, with no LLM in classification or materiality. It runs these stages in order:

1. Collect raw changes.
2. Normalise them into typed events.
3. Deduplicate.
4. Apply the documented materiality rules.
5. Map competitor impacts.
6. Rank Needs Attention.
7. Generate questions and suggested actions.
8. Build proxy metrics with comparable windows.

It produces two reports: a per-company review memo and the daily Portfolio brief, both from PORTFOLIO ANALYST, evaluated by the V3 control plane.

**Sources.** New sources are kept to a minimum:
- **Company newsroom, blog and changelog items.** These are dated posts from pages the crawler already fetches, or from an RSS/Atom feed the site itself declares. They carry the launches, financings, contracts and partnerships that companies announce publicly.
- **USAspending.gov awards.** This is the official public federal award API, with no key. It is queried only for portfolio companies and comps, by exact recipient name.

**API and UI.**
- `/api/portfolio*`.
- `/portfolio` (Pulse), `/portfolio/[company]`, `/portfolio/events` (with detail pages), `/portfolio/comps` and `/portfolio/brief`.
- Company page tabs OVERVIEW / RADAR / DILIGENCE / PORTFOLIO / COMPS / RELATIONSHIPS / REPORTS.
- A `/demo/portfolio` tour.

## 4. Risks

- **Identity mix-ups.** Short or common names (Merge, Orbital, Trajectory, Enigma, Aeon) collide with unrelated organisations. For example, the `trajectory-ai` GitHub org and the Orbital Ashby board belong to someone else. Mitigation: only identifiers verified by hand are configured, and name-only search (HN, arXiv, USAspending) requires the exact name plus corroboration by domain or link.
- **Sparse public data.** Seed-stage companies have little public footprint, so they will often show *no material change*. That is a true result, and it must not be padded.
- **No baseline for new companies.** Most portfolio companies were not observed on Oct 20 or Nov 5. "Since last review" therefore uses windows dated by event (last 14 days vs the prior 14, from posting, release and publication dates), plus observed deltas only between two V4 captures. Pairs that cannot be compared say so.
- **Financing is only what companies announce.** There is no funding database. A round appears only if the company or a credible public story says so.
- **Competitor relevance is judgement.** Comps are INFERENCE, each with a source, and confidence is shown.
- **Noise.** Website copy changes, documentation edits and routine releases dominate raw change counts. Every one is counted, and they are filtered by rule and shown as filtered, not hidden.
- **Untrusted content.** Newsroom pages and feeds are external text. They go through the SSRF-guarded client and the injection guard, and are never executed.

## 5. Public-data limitations (stated on the site)

**Never available in public mode:**
- ownership, cost basis, marks, reserves, round participation;
- board status, internal owner, internal notes;
- private revenue, customer counts and internal metrics.

These fields exist in the schema for a future firm mode. They are never populated or serialised publicly.

**Not covered by sources:**
- X attention without API access;
- press coverage beyond Hacker News;
- private hiring channels;
- contracts below federal reporting thresholds or outside USAspending;
- anything a company has not made public.

**Proxies are not internal metrics.** Proxy metrics are labeled as public proxies: "12 open technical roles" is a hiring signal, not headcount.

## 6. Implementation plan

1. Public portfolio configuration (identities verified by hand) and comps with sources.
2. Migration 0008 and models; position, private-field boundary.
3. Collectors: newsroom/blog items, USAspending awards; `radar demo capture --update --portfolio` (real capture).
4. Engine: candidates → normalise → dedup → materiality (rules M1–M10) → impacts → attention → questions/actions → metrics → reports. Wired into `post_seed_steps` and a `radar portfolio` CLI.
5. PORTFOLIO ANALYST in the agent control plane (checks, cases, documented failures).
6. API, then frontend (Pulse, company portfolio view, events, comps, brief, company tabs, reviews on a writable backend only).
7. Hand audit of the top events and every Needs Attention card. Fix false positives, file them in the failure ledger with regression cases.
8. A real V3 diligence run on one portfolio company, for the handoff.
9. Second capture; V4 snapshot; `/demo/portfolio`; deploy; README; `docs/PORTFOLIO_METHODOLOGY.md`; `docs/V4_READY.md`.

Out of scope, as V4 requires: LP CRM, fund accounting, cap tables, board portal, chat, billing, mobile, new graph database, patent ingestion, recommendation or buy/sell engines.
