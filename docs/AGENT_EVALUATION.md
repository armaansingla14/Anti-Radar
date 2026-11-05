# Agent evaluation

ANTI RADAR's agents write research that a partner may act on. This control plane measures what they deliver,
records where they failed, and turns each failure into a regression case. It runs offline and
deterministically: no metric needs a model, and no metric is estimated. If a value cannot be computed it is
`null` with a reason.

Code: `backend/radar/evals/` · API: `/api/agents*` · CLI: `radar eval` · cases: `backend/tests/evals/cases/`.

## Agents

| Agent | Writes (report kind) | LLM purposes | Evaluated by |
|---|---|---|---|
| RADAR ANALYST | `brief` | `analyst.investigate`, `analyst.brief` | report evaluator + cases |
| MEETING BRIEF | `meeting_brief` | `analyst.meeting_brief` | report evaluator + cases |
| ANTI MEMO | `memo` | `analyst.memo` | report evaluator |
| MORNING BRIEF | `morning_brief` | `narrative.morning_brief` | structure, trace and timing only (no source-cited claims) |
| DILIGENCE ANALYST | `diligence` | `diligence.extract` | structural diligence rules |
| NARRATIVES | none | `narrative.why_now/why_anti/risks/paper` | LLM telemetry only |
| SEARCH PARSER | none | `search.parse` | LLM telemetry only |
| THESIS CLASSIFIER | none | `topics.refine` | LLM telemetry only |

These deterministic components own failures but write no evaluated report: FORMATION SCORER, MEET RANKER,
ENTITY RESOLUTION, PRESENTERS (page copy). Their failures are guarded by pytest tests and pure regression cases.

## Two views, kept apart

Every stored report has already passed `validate_citations`. That guard removes citations to sources that do
not exist and downgrades an uncited FACT to `INFERENCE … (unverified)`. As a result:

- **Delivered metrics** are computed from the body the user reads and stored in columns. Delivered citation
  validity is therefore normally 1.0.
- **Guard interventions** are what the model produced before the guard repaired it. The only record of this
  is the trace's `validation` entry. These become flags (`guard_removed_invalid_ids`,
  `guard_downgraded_fact`) and are **never** counted in the delivered unsupported rate.

A high delivered score with many guard flags means the model is producing bad citations that the guard is
catching. Both numbers matter, so they are reported separately.

## Metric definitions

Per report (`agent_run_evaluations`, one row per report and evaluator version `eval-1`):

| Metric | Definition |
|---|---|
| task_success | status `complete` and the body validates against the output schema |
| structured_output_valid | the body validates (`AntiBrief`, `AntiMemo`, `MeetingBrief`, `MorningBrief`; diligence: `DiligenceReport` when present, else a structural check) |
| claims_total / fact_claims | every `{text, epistemic, source_ids}` in the body / those labelled FACT |
| citations_total / invalid_citations | delivered citation ids / those not among the report's sources |
| citation_precision | (citations − invalid) / citations. This is **citation validity: existence, not semantic support** |
| unsupported_claims, unsupported_claim_rate | claims hit by an "unsupported" rule (below) / claims_total; null when there are no claims |
| source_coverage | registered sources cited by at least one claim / registered sources; null with no sources |
| sources_used | distinct registered sources cited |
| latency_ms | `ms` of the trace's final `timing` entry (perf_counter). Null for reports generated before timing was recorded |
| cost_usd | the report's own `cost_usd` (LLM calls are not linked to reports) |
| fallback | a live LLM was attempted and the template replaced it. No LLM configured is not a fallback. Not recorded for ANTI MEMO |
| error | status is not `complete` |

Per agent (`GET /api/agents`), every value is `{value, n, reason}` and uses only rows at the current
evaluator version: `success_rate`, `structured_validity_rate`, `error_rate`, `fallback_rate` (n = runs);
`citation_validity` (pooled, n = citations); `unsupported_claim_rate` (pooled, n = claims); `source_coverage`
(mean over runs with sources); `avg_sources_used`; `latency_p50_ms` and `latency_p95_ms` (nearest rank over runs
that have a timing entry); `avg_cost_usd`; `human_review_pending` (a count: needs-review flags on unreviewed
runs plus OPEN failures); `by_generator`; `flags_by_category`. LLM telemetry is grouped by purpose from
`llm_calls` (latest 20,000): calls, ok rate, p50/p95 latency of live calls (cache hits excluded), cost,
cache-hit rate, models. `METHODOLOGY` in `radar/evals/metrics.py` is the source of these texts, and the API
serves it.

## Automatic rules

| Rule id | Category | Severity | Counted as unsupported? |
|---|---|---|---|
| `fact_without_citation` | UNSUPPORTED_CLAIM | HIGH | yes |
| `nonexistent_citation` | CITATION_MISMATCH | HIGH | yes, when no valid citation remains |
| `source_may_be_unrelated` (FACT; none of its numbers, quoted strings or non-initial capitalized words appear in the cited title + snippet) | CITATION_MISMATCH | LOW, needs human review | no (counted as `possibly_unrelated`) |
| `affiliation_from_event_source` (FACT says works at / joined / currently at…, not negated, and every cited source is a talk, conference, paper, HN or video) | AFFILIATION_INFERENCE | HIGH | yes |
| `funding_without_funding_source` (raised / seed round / Series A / valuation / $12M…, not negated, no press / announcement / filing source) | UNSUPPORTED_CLAIM | HIGH | yes |
| `customer_from_own_marketing` (customers / used by / trusted by… cited only to the company's own pages) | SOURCE_QUALITY | MEDIUM | no |
| `hedged_fact` (FACT with likely / might / suggests / probably… outside quotes; lowercase "may" only, so "May 2026" is not a hedge) | OVERCONFIDENT_INFERENCE | MEDIUM | no |
| `stale_source` (cited source fetched > 90 days before the report) | STALE_INFORMATION | LOW | no |
| `injection_in_sources` (injection patterns in a source, from the trace or the cited content) | PROMPT_INJECTION_ATTEMPT | INFO | no |
| `output_follows_injection` (injection patterns in a claim, question or list item of the output) | PROMPT_INJECTION_ATTEMPT | HIGH | no |
| `tool_error` / `run_error` | TOOL_FAILURE | LOW / HIGH | no |
| `llm_fallback` | STRUCTURED_OUTPUT_FAILURE (invalid / schema / parse / truncated) or TOOL_FAILURE | MEDIUM | no |
| `guard_removed_invalid_ids` / `guard_downgraded_fact` | CITATION_MISMATCH / UNSUPPORTED_CLAIM | MEDIUM / LOW | no (guard view) |
| `diligence_claim_unsourced` | UNSUPPORTED_CLAIM | HIGH | yes |
| `diligence_status_without_independent_evidence` (a status other than UNVERIFIED / UNTESTABLE with no non-company evidence) | UNSUPPORTED_CLAIM | HIGH, needs human review | yes |

`radar eval` promotes every HIGH flag to an OPEN failure (origin `evaluator`), de-duplicated by
(report, rule, claim text).

## What needs human review

- `source_may_be_unrelated`: token overlap cannot judge support. A human decides whether the source says it.
- Every promoted HIGH flag: confirm, fix or dismiss it in the failure ledger (`POST
  /api/agents/failures/{n}/review`). A review is recorded as a `ReviewDecision`, appended to the resolution, and
  marks the report's evaluation `HUMAN_REVIEWED`. Re-evaluation keeps the review status and notes.
- Diligence statuses that claim support without independent evidence.

## Taxonomy

| Category | Definition |
|---|---|
| UNSUPPORTED_CLAIM | A statement presented as established that no cited source supports. |
| CITATION_MISMATCH | A citation that does not exist, or whose source does not appear to say what the claim says. |
| AFFILIATION_INFERENCE | Employment or affiliation inferred from weak evidence (a talk, an event, a paper, a self-reported field) and stated as fact. |
| OVERCONFIDENT_INFERENCE | An inference labelled or ranked with more confidence than its evidence allows. |
| MISSING_COUNTEREVIDENCE | Available evidence against the conclusion was ignored. |
| STALE_INFORMATION | A claim rests on a source too old to describe the present. |
| SOURCE_QUALITY | The cited source is the wrong kind for the claim (own marketing for customers, noise repositories for formation, partial coverage for team convergence). |
| ENTITY_RESOLUTION_ERROR | Records about different entities merged, or a non-person (bot, tool account) treated as a person. |
| RANKING_ERROR | A deterministic score or ranking placed an entity where its evidence does not put it, without any single claim being wrong. |
| PROMPT_INJECTION_ATTEMPT | A source contained instructions aimed at the model. INFO when the output ignored them; HIGH when it followed them. |
| TOOL_FAILURE | A tool call, fetch or model call failed during the run. |
| STRUCTURED_OUTPUT_FAILURE | The output did not match its schema, or invalid model output was replaced by the template. |

## Regression workflow

1. A failure is found (evaluator flag, review, or a real-world evaluation) and recorded in the ledger.
2. A case is added to `backend/tests/evals/cases/<id>.yaml`. The id is the file name. Each case has a title,
   agent, task, failure_category, input and expected values. Tasks are registered in
   `radar/evals/runner.py`, and new tasks plug in with `register_task(name, fn, needs_db=…, llm_capable=…)`.
3. Every case runs as a pytest test: `tests/evals/test_regression_cases.py::test_regression_case[<id>]`. Pure
   cases need no database. Cases that build fixtures run in a savepoint that is always rolled back.
4. The failure's `regression_case` and `regression_test` point at the case and the node id. A test checks that
   every referenced node id resolves to a real test function.
5. `radar eval` evaluates stored reports, promotes HIGH flags, runs the cases with the template, stores the
   results (`eval_case_results`), and exits 1 if any case fails. Options: `--agent analyst`,
   `--case affiliation-001`, `--seed-failures`, `--compare-models`, `--json`.

## Documented failures

Seeded by `radar eval --seed-failures`. Seeding is idempotent and never overwrites a status a human changed.

| # | Failure | Owner | Category | Severity | Status | Guard |
|---|---|---|---|---|---|---|
| 1 | youkaichao and Simon Mo: POSSIBLE FORMATION (Inferact), already public and funded (§6.1) | FORMATION SCORER (+ MEET RANKER) | MISSING_COUNTEREVIDENCE ¹ | HIGH | FIXED | `public-founder-org-001`; `test_api_meet.py::test_cofounder_of_an_already_public_company_gets_the_obviousness_penalty` |
| 2 | Roger Wang still #4 in Meet (§6.2) | MEET RANKER | MISSING_COUNTEREVIDENCE | MEDIUM | CONFIRMED (mitigated) | `current-org-elsewhere-001`; `test_formation_score.py::test_current_org_elsewhere_needs_company_and_public_membership_of_an_old_org` |
| 3 | smg-project team convergence is a coverage artifact (§6.3) | FORMATION SCORER | SOURCE_QUALITY | MEDIUM | CONFIRMED | none (not fixed) |
| 4 | Pareton formation #2 (§6.4) | FORMATION SCORER | SOURCE_QUALITY | MEDIUM | FIXED | `test_formation_score.py::test_unverified_team_without_traction_is_not_a_candidate` |
| 5 | Hobby repositories as formation evidence (§6.5) | FORMATION SCORER | SOURCE_QUALITY | MEDIUM | FIXED | `new-project-noise-001`; `test_formation_score.py::test_new_project_noise_filter_and_traction` |
| 6 | Muennighoff: 27★ as two evidence kinds (§6.6) | FORMATION SCORER | OVERCONFIDENT_INFERENCE | LOW | FIXED | `notable-traction-001`; `test_formation_run.py::test_new_project_traction_must_be_notable_to_be_its_own_evidence` |
| 7 | Unknown contributors leading Meet (§6.7) | MEET RANKER | OVERCONFIDENT_INFERENCE | MEDIUM | FIXED | `test_meet_ranking.py::test_verified_talent_with_traction_outranks_unknown_young_org_contributor_on_features` |
| 8 | Zhengyi Luo: bio says currently at NVIDIA (§6.8) | MEET RANKER | AFFILIATION_INFERENCE | MEDIUM | CONFIRMED | none (not fixed) |
| 9 | why_anti "Strong technical founding team." (FINAL_APPLICATION_AUDIT #8) | PRESENTERS | UNSUPPORTED_CLAIM | MEDIUM | FIXED | `why-anti-copy-001` (no pytest test existed before this case) |
| 10 | AI coding-agent accounts as team members (commit 125f81a) | ENTITY RESOLUTION | ENTITY_RESOLUTION_ERROR | HIGH | FIXED | `bot-accounts-001`; `test_github_provider.py::test_ai_coding_agents_are_bots` |

¹ #1 could also be STALE_INFORMATION. It is filed as MISSING_COUNTEREVIDENCE because the evidence that the
formation was already public (the founders' own bios and the organisation's age) was in our data and was
ignored.

The § references are to `docs/REAL_WORLD_EVALUATION.md`. Related tests for each row are listed in the failure's
evidence summary.

## Model comparison

`radar eval --compare-models` runs the LLM-capable cases (`meeting_brief_template`, `anti_brief_template`,
`injection`) once with the template baseline and once per configured model. A model is configured only when its
provider has an API key: Anthropic fast/standard/deep, OpenAI fast/deep, de-duplicated. With no key the command
prints `no models configured` and exits 0. Results are normalized per (task, provider, model): n, pass rate,
citation validity, unsupported-claim rate (pooled), coverage, p50 latency, cost per case and structured
compliance.

Recommendation rule, per task:

1. Eligible models need at least 10 results. The template is a candidate, because "the template is enough" is a
   legitimate answer. With fewer than two eligible models the recommendation is null, and the reason states the
   data available (`insufficient evaluation data: …`).
2. The quality reference is the eligible model with the best pass rate (ties go to the lower unsupported rate).
3. The floor is pass rate ≥ best − 0.05 and unsupported rate ≤ reference + 0.02.
4. Among models above the floor, the cheapest mean cost per case is chosen, tie-broken by p50 latency and then by
   name. The reason cites the numbers.

## Limitations

- Semantic support is not judged automatically. Citation validity is existence only, and the token-overlap rule
  only asks a human to look.
- Stored source snippets are 500 characters, so the token check is conservative: it may flag a source whose
  supporting text lies beyond the snippet. It never marks a claim unsupported.
- Staleness depends on `fetched_at`, which for database-backed sources is the registration time (about the
  report time). It fires only on live-fetched or explicitly dated sources.
- The memo does not record an LLM fallback, so its fallback rate is null.
- Latency exists only for reports generated after the timing entry was added. The memo's timing includes the
  nested template investigation when no brief existed.
- LLM calls are not linked to reports, so per-report cost is the report's own `cost_usd`.
- Ranking failures (Formation, Meet) are deterministic and guarded by pytest and pure cases, not by the report
  evaluator.
- The morning brief has no source-cited claims, so its claim metrics are null.
- Diligence uses structural rules. The `DiligenceReport` schema is used when the module is present.
