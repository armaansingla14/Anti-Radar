# V3 audit: what exists, what V3 extends

Written on Nov 4, 2026, before the V3 implementation, by reading the code; file references are relative to `backend/radar/` unless marked. V3 adds three capabilities on top of Radar:

- **Since last snapshot:** what changed between verified snapshots.
- **Technical Diligence:** a claim ledger, evidence, contradictions, test plans and underwriting.
- **The Agent Evaluation control plane:** metrics, failures and regression cases.

Portfolio monitoring is out of scope (V4).

## 1. Reusable infrastructure

| Area | What exists | How V3 reuses it |
|---|---|---|
| Agent research | `agent/analyst.py` `investigate()` gathers a fixed evidence pass into a `SourceRegistry` (`agent/tools.py`, ids S1…), optionally runs an LLM tool loop, and always has a deterministic `template_brief` fallback | Diligence follows the same split: deterministic first, with an LLM only as an optional assistant. Its sources use the same `{id, url, title, source_type, fetched_at}` shape, so the existing report UI renders citations. |
| Report system | `ResearchReport` (kind, body JSON, markdown, sources, model, generator, status, trace, cost) with kinds brief, memo, morning_brief and meeting_brief; `api/routes/reports.py`; frontend `app/reports/[id]` | Diligence is a new report kind, `diligence`, with a `DiligenceReport` body. No parallel reporting system. |
| Epistemics | `Claim{text, epistemic: FACT/INFERENCE/MODEL_OPINION, source_ids}` (`agent/brief_schema.py`); `llm/guard.validate_citations` strips unknown ids and downgrades an uncited FACT to INFERENCE | The evaluator walks the same claim shape. Its citation checks extend `validate_citations` (does the source exist, is it related, is it authoritative enough, is the label right). |
| Untrusted content | `llm/guard.py` (`detect_injection`, `wrap_source`, `escape_content`, `SYSTEM_GUARD`); `providers/http.py` (SSRF guard, per-hop redirect checks, 3 MB cap, deadlines, `RobotsCache`); `providers/website.py` `extract_page` | All diligence collection goes through these. Artifacts are flagged on injection patterns and never executed. |
| GitHub data | `providers/github.py` already reads repo metadata (license, archived, stars, forks, PR count), README, weekly commits, contributor commit counts with first and last dates, and releases | Open-source health (bus factor, concentration, cadence) is computed from the same calls. Diligence adds the file tree, manifests, closed issues and advisories. |
| Telemetry | `LLMCall` (purpose, model, tokens, cost, cache_hit, latency_ms, ok, error); daily cost cap; report `trace` | Evaluation aggregates `LLMCall` by purpose. Report-level metrics come from report bodies and traces. |
| Scoring history | `ScoreSnapshot` per entity per day with a `version` (`SCORE_VERSION` "2026.10-1"); `presenters.comparable_baseline` already refuses cross-version comparisons; reconstructed rows are flagged | "Since Oct 20" compares live rows of the same version only. |
| Snapshot mode | Capture → replay (`demo/seed.py`); a frontend bundle of recorded API responses (`frontend/snapshot`, `DATA_SOURCE=snapshot`) | Extended to a **chain**: the October base plus dated updates, replayed in order, then exported. |
| Formation / Meet | `scoring/formation.py`, `services/meet.py` (per ISO week), rejection reasons, `explain()` | Formation gets per-day history for NEW / STRENGTHENED / WEAKENED / DROPPED. Meet weeks are compared directly. |
| UI | Editorial primitives (`SectionLabel`, `Section`, `Contents`, `Cite`, `Sources`, `EpistemicBadge`, `VerificationBadge`, `Delta`, `StatTile`, `PageHeader`), `ReportBody`, `RouteError`, loading skeletons, `useStaticSnapshot` | The Diligence, Agents and comparison UIs are built from these. A `ChangeBadge` sits next to `NewBadge`. |

## 2. What needs extension

- **Report kinds** are hard-coded in two places: `api/routes/reports.py` (the kind filter) and `services/entities.py` `report_filename`. The frontend `app/reports/[id]` maps kinds to section lists. `diligence` must be added to all three.
- **`LLMCall` has no link to a report or run.** Per-report latency and cost therefore come from the report's own `trace` and `cost_usd`; per-purpose telemetry comes from `LLMCall`. V3 adds a `timing` trace entry to every agent output, and older reports keep latency unknown rather than estimated.
- **Formation candidates keep only current state:** rows that stop qualifying are deleted. A per-day `formation_history` table is required.
- **Meet** computes with the clock's today, whatever week is requested, so every snapshot link must compute its own week while the clock is pinned to that link.
- **Seeding** replays one snapshot. Adding a chain introduces three hazards: reconstructed history overwriting the real base-day score, `backfill_first_seen` back-dating newly discovered entities, and the clock pin reading only the base manifest.
- **Navigation** (`frontend/lib/routes.ts`) drives the nav, palette and shortcuts. Two frontend tests pin the exact nav and proxy lists.
- **The `/demo` presenter** is hard-wired to one tour (ten scenes). A Diligence tour needs it parameterised.

## 3. Data model migrations (0007)

- `formation_history`: one row per candidate per scored day.
- `diligence_runs`, `diligence_artifacts`, `diligence_claims`: collection is kept separate from analysis, so a captured run replays offline. Every claim keeps its verbatim source quote and its evidence for and against.
- `review_decisions`: a human ACCEPT / QUESTION / REJECT / NEEDS VERIFICATION next to the agent's assessment.
- `agent_run_evaluations`, `agent_failures`, `eval_case_results`: per-output evaluation, the failure ledger, and regression and model-comparison results.

No existing columns change. `ScoreSnapshot.version` already carries the scoring method.

## 4. UI to reuse

- Company and person pages: `EntityHeader`, `Section`/`Contents`, `WhyNowLead`, `WhatChanged`, `Delta`. The tabs use the existing plain-link strip pattern (`FilterStrip`).
- Reports: `ReportBody`, `ClaimLine`, `Cite`, `Sources`, `GeneratorLabel`.
- Badges: `VerificationBadge` and `EpistemicBadge`, plus the shared chip class in `Badges.tsx`.
- Index pages: `PageHeader`, `StatTile`, the table styles in `entity/Lists.tsx`, `EmptyState`.
- The demo presenter engine (`components/demo/*`) for the Diligence tour.

## 5. Evaluation telemetry that already exists

- `LLMCall` rows per purpose, including `analyst.investigate` (one per tool-loop turn), `analyst.brief`, `analyst.meeting_brief`, `analyst.memo`, `narrative.*`, `search.parse` and `topics.refine`.
- Report traces: validation counts (`total_claims`, `invalid_ids`, `downgraded`, `ok_ratio`), injection flags, fallback notes, and counts of gathered evidence.
- Status aggregates in `services/status.py`: calls and cost in the last 24 h, cache hit rate.
- **No eval or golden-set tests existed.** `retro.py` explicitly says it is not a backtest.

## 6. Risks and technical debt found

- **No LLM in this environment.** `LLM_PROVIDER=none`, and the only key in the shell belongs to an unrelated work account, which is never used here. Every agent therefore runs its deterministic path. Evaluation metrics describe those deterministic outputs. Model comparison ships as working infrastructure and honestly reports "no models configured" until keys are added.
- **Score percentiles are cohort-relative.** A larger November cohort moves scores even when a company did not change. The comparison says so and only compares re-observed entities.
- **Capture breadth differs between snapshots.** "New" means new to Radar, not newly created.
- **Strange signals are computed live and never persisted,** so they can't be compared across snapshots (out of scope).
- **The synchronous `POST /diligence/{company}/run`** may exceed a hosting proxy's timeout. Long runs belong in a job queue later.
- **Reports generated in a database but not captured** (for example, template briefs) vanished on reseed. V3 regenerates them deterministically during the seed.
- **Retention** thins evidence after 14 days and snapshots after 90. A long-lived live database would need retention rules for the new tables (not urgent; they are small).
