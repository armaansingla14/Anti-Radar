# V3 ready: Discover. Diligence. Decide.

| | |
|---|---|
| **Live URL** | https://anti-radar-one.vercel.app |
| **Diligence URL** | https://anti-radar-one.vercel.app/diligence/unsloth-ai (index: [/diligence](https://anti-radar-one.vercel.app/diligence)) |
| **Agent Eval URL** | https://anti-radar-one.vercel.app/agents (failure ledger: [/agents/failures](https://anti-radar-one.vercel.app/agents/failures), cases: [/agents/cases](https://anti-radar-one.vercel.app/agents/cases)) |
| **Demo URL** | Video: https://anti-radar-one.vercel.app/video#diligence (2:15, captioned, silent). Tour: https://anti-radar-one.vercel.app/demo/diligence (9 scenes) |
| **What changed** | https://anti-radar-one.vercel.app/changes (Oct 20 → Nov 5) |
| **GitHub** | https://github.com/armaansingla14/Anti-Radar |
| **Snapshot date** | **Nov 5, 2026**: live public data captured on top of the Oct 20, 2026 snapshot (`backend/radar/demo/snapshot_updates/2026-11-05/`, 42 batches) |
| **Entities analyzed** | 368 companies and organizations, 4,567 people, 7 formation candidates, 12 stored reports |
| **Diligence demo subject** | Unsloth AI (training infrastructure; unsloth.ai, the `unslothai` GitHub org, PyPI, Hacker News) |
| **Claims evaluated** | 12 material claims from 99 public artifacts: 2 verified, 5 supported, 0 partially supported, 0 evidence conflict, 5 unverified, 0 untestable. Evidence coverage 58%. 6 founder questions. |
| **Agent evaluation cases** | 16 YAML regression cases (16/16 passing). Failure ledger: 20 documented failures, 18 pinned by a regression test, 13-category taxonomy. |
| **Test totals** | Backend 1,227 (800 unit, 410 integration on real Postgres, 17 eval regression); frontend 177 (`node --test`); Playwright `/demo` and `/demo/diligence` tours; public verifier 70/70 on the snapshot build with no backend |
| **CI status** | Green on `main` ([CI run for c99dc37](https://github.com/armaansingla14/Anti-Radar/actions/workflows/ci.yml)): backend lint + full suite on Postgres, frontend typecheck/lint/tests/build, and the snapshot-site smoke test with no backend |

## Since Oct 20 (real, from `/api/changes`)

- **New to Radar:** 148 companies, 1,763 people, 130 repositories, 322 papers, 882 signals, 12 job postings. "New" means new to Radar, not newly created.
- **Score movers:** 47 companies were compared on the same scoring version (2026.10-1); 173 were not re-observed and are not compared.
  - 5 accelerated (Xiaomi Robotics +14).
  - 11 weakened (Unsloth AI −16, vLLM −10).
- **Formation:**
  - NEW: smg-project, Valen-Team, AMIT SHEKHAR.
  - STRENGTHENED: Kevin Zakka, 40.4 → 45.4.
  - DROPPED, each with its reason: Simon Mo, formation now public; Jun Penn, below threshold.
- **Meet W43 → W45:** Yineng Zhang and Roger Wang entered; Simon Mo and Simo Lin left.

## How it was verified

- **Diligence ledger, hand audit.** Every claim quote was checked against its source page and every status against rules R1–R6.
  - The audit found 9 real extraction and matching errors. Each was fixed, filed in the failure ledger and pinned by a regression test.
  - All 22 cited sources in the deployed workspace return HTTP 200.
- **The evaluator found a real bug itself.** The injection guard flagged "Founder Score 48/100" in meeting briefs as prompt injection. It was fixed, recorded as FAILURE 0011 (origin: automatic evaluator), and pinned by case `injection-false-positive-001`.
- **Demo claims.** Every number in the tour is computed from the data at runtime. The numbers in [`demo/DILIGENCE_DEMO_SCRIPT.md`](../demo/DILIGENCE_DEMO_SCRIPT.md) were checked against the deployed snapshot: 12 claims (2/5/0/0/5/0), 58%, 99 sources, 6 questions, 18 of 20 failures pinned.
- **Production.**
  - `node scripts/verify_public_demo.mjs` against production passes all 65 HTTP, asset and README-link checks, including `/changes`, `/diligence/unsloth-ai`, the claim page, `/agents`, `/agents/failures`, `/agents/cases`, `/demo/diligence` and both videos.
  - The browser journey has two runs. It passed 70/70 against the identical snapshot build locally. On this development machine, the browser run against production is blocked by a TLS-intercepting proxy, so it is not a site failure.
- **Safety.**
  - Collection uses an SSRF guard (private and link-local addresses refused, redirects re-checked), respects robots.txt and caps artifact sizes.
  - Artifacts are stored as data and never executed; test plans are generated, not run.
  - External text is wrapped as untrusted, and injection attempts are flagged.
  - Secrets found in public code are masked.
  - No vulnerability is claimed without advisory data (OSV).

## Acceptance questions

| Question | Answer |
|---|---|
| Can Radar find a company? | Yes. Unsloth AI is on the radar, and its −16 since Oct 20 is shown as plainly as a gain. Every company row links to its diligence workspace when one exists. |
| Can Diligence explain what that company claims? | Yes: 12 material claims, each a verbatim quote with its source URL and date. |
| Can every claim be traced to evidence? | Yes. Every evidence item links to its artifact (S-ids), and gaps are listed explicitly. |
| Does it actively look for contradictory evidence? | Yes. Contradiction detectors run over code, registries and third-party sources. None was found for Unsloth, and the page says so. |
| Does it distinguish company claims from independent evidence? | Yes. Evidence is split into company / public artifact (code, registry) / third party. A company's own statement never verifies its claim (R6). |
| Does it say UNKNOWN when evidence is missing? | Yes. 5 claims are UNVERIFIED, underwriting lists its unknowns, and coverage is 58%, not rounded up. |
| Can it identify which claims are testable? | Yes. Each claim is marked HIGH / MEDIUM / LOW / NOT TESTABLE, with the reason. |
| Can it generate a useful technical test plan? | Yes, for HIGH and MEDIUM claims: baseline, candidate, hardware, workload grid, metrics, procedure and pass criteria. The plans are generated, never executed. |
| Does technical underwriting avoid fake precision? | Yes. There is no overall score. Each category has an evidence-strength or risk rating with known / inferred / unknown items, and STRONG needs independent support. |
| Are founder questions derived from evidence gaps? | Yes. Each question carries "why this question" and the claims or evidence it comes from (e.g. the AGPL-3.0 `studio/` licence). |
| Can I see how reliable the research agent is? | Yes, at `/agents`. Every metric has n, and a value that is not measured shows its reason. |
| Can I inspect real agent failures? | Yes. There are 20 entries in `/agents/failures`, all real: 10 from the Oct 20 evaluation, the guard false positive, and 9 from the diligence audit. |
| Can confirmed failures become regression cases? | Yes. 18 of 20 are pinned by a pytest test or YAML case. The other 2 are marked as having no test. |
| Can I compare models using measured evidence? | The machinery exists (`radar eval --compare-models`, opt-in via `EVAL_PROVIDERS`), but no models are configured on the public deployment. The page says so and shows no recommendation, rather than an opinion. |
| Does the public demo work without a backend? | Yes. Vercel serves the bundled snapshot (`DATA_SOURCE=snapshot`), and the verifier ran against a build pointed at a dead backend. |
| Is the November snapshot real? | Yes. It was captured live on Nov 5, 2026 from public APIs. The batches are committed and replayed deterministically. |
| Does the Oct → Nov comparison show actual change? | Yes: 148 new companies, 16 material score moves in both directions, formation NEW/STRENGTHENED/DROPPED with reasons, and Meet entered/left. |
| Would an investor / technical founder / engineer trust it? | It is built to be checked: verbatim quotes, linked artifacts, rule ids, explicit unknowns, no recommendation, no fabricated reviews, and agents measured with their failures in public. |
| Would Anti Fund plausibly use this during a real deal? | As a first pass before a technical call: what the company claims, what public evidence supports, and which questions to ask. It does not replace the call or access to private material. |

## Known limitations

- **Public artifacts only.** Private code, customer results and internal benchmarks are invisible, so performance claims usually end UNVERIFIED until the company shares evidence.
- **One diligence workspace in the public snapshot** (Unsloth AI); more can be run with `radar diligence run <company>` on a live backend.
- **No human reviews exist yet.** The snapshot shows "Awaiting human review", and review actions work only on a writable backend.
- **Model comparison is unconfigured publicly**, so there is no "recommended model by task".
- **Score comparison is partial.** Only re-observed companies are compared (47 of 220).
- **The diligence video is silent and captioned.** The radar video (Oct 20) is narrated, and its numbers predate the November snapshot.
- **Formation remains inference, not proof**, and X and press attention remain unavailable without API access.

## Final product status

V3 is complete and deployed:
- **RADAR:** who is becoming interesting.
- **FORMATION:** who may be starting something.
- **MEET:** who to talk to.
- **DILIGENCE:** what we should believe.
- **AGENTS:** whether the research system can be trusted.
- **THESIS** and **REPORTS.**

Portfolio monitoring (V4) was intentionally not built.
