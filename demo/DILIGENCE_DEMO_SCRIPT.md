# Anti Radar: technical diligence demo script (2:16)

The tour lives at `/demo/diligence`. It follows one company from the radar into its diligence workspace, then turns to the agents that did the work and audits them. The recording is `demo/anti-radar-diligence-demo.mp4`: a silent cut at 1920×1080 with captions, 2:16 long. It was made against the Nov 5, 2026 data with `TOUR=diligence BASE_URL=http://localhost:3010 e2e/record-demo.sh`.

The tour picks everything at runtime from the API, so nothing is hard-coded:

- **Workspace:** the most recently finished diligence workspace.
- **Radar row:** where that company shows up in Radar. That is its row in the since-snapshot lists on `/changes`, or its company page if it isn't in those lists.
- **Claim:** the most testable PERFORMANCE claim that is still UNVERIFIED.
- **Underwriting cards:** the highest risk-scale card, then the first evidence-scale card.
- **Founder question:** the critical founder question.
- **Agent failure:** a failure the automatic evaluator caught in the injection guard, and the regression case that pins it.

On other data the names and numbers change. Read the caption in the presenter bar for each scene and swap them in. Every number below was on screen in this take.

Things to say if asked:

- **Nothing here is an investment recommendation.** Statuses are evidence assessments from documented rules (R1–R6). CONTRADICTED is shown as "EVIDENCE CONFLICT", which means something to resolve with the company. It is never called deception.
- **Test plans are generated, not run.** Untrusted code is never executed on the radar host.
- **No language model wrote any of this.** Diligence comes from rules over collected public artifacts, and LLM telemetry is empty.
- **Human review is separate.** Every claim and underwriting card shows the agent's assessment next to the human review. Here that is "Awaiting human review", and no review is ever invented.

| # | Time | URL | Caption (presenter bar) | On screen | Narration (optional) |
|---|------|-----|-------------------------|-----------|----------------------|
| — | 0:00 | `/demo/diligence` (title card) | — | ANTI RADAR · *Technical Diligence.* · Start | Radar finds technical change early. Diligence asks what a company claims and whether the public evidence holds up. |
| 1 | 0:11 | `/changes` | Unsloth AI — Anti Score 13, −16 since Oct 20: weakened. Radar shows a decline as plainly as a gain. | "Since last snapshot": Unsloth AI is first in **Weakened** (29 → 13), with **Open diligence →** next to its name | Radar doesn't cherry-pick. Unsloth AI fell 16 points since October 20th, and it's at the top of the weakened list. It also has a diligence workspace. |
| 2 | 0:23 | `/diligence/unsloth-ai` (opened by clicking the row's link) | Diligence status NEEDS REVIEW — 1 pending review: Open-source health: 61% of commits come from one contributor (danielhanchen) · coverage 58% · 99 sources analyzed | Workspace header: NEEDS REVIEW badge with its reason, collected Nov 5, evidence coverage 58%, 99 sources analyzed, method `dil-2026.11-1`, replay mode | 99 public artifacts were collected and analyzed. The status is "needs review", and it says why: one contributor wrote 61% of the commits. |
| 3 | 0:33 | same | 12 material claims: 2 verified · 5 supported · 0 partially supported · 0 evidence conflict · 5 unverified · 0 untestable | Executive summary counts, strongest evidence, largest uncertainty, critical founder question, then the claim ledger (claims 01–09 visible) | Twelve material claims. Two are verified and five supported. Five rest only on the company's word. There are no evidence conflicts. |
| 4 | 0:46 | `/diligence/unsloth-ai/claims/1` | CLAIM 01, performance, testability high — evidence: company 2 · public artifact 14 · third party 2 · R6: only the company's own statement → UNVERIFIED | The claim in full ("…finetune Llama with QLoRA 5x faster than Huggingface's original implementation…"), then evidence grouped as company / public artifact / third party, with gaps | Claim one: five times faster than Hugging Face. The company says it twice. Its code targets Hugging Face and Llama, but nothing independent reproduces the figure, so rule R6 applies and it stays unverified. |
| 5 | 0:56 | same | Generated, not run — baseline Huggingface, 2 metrics, 7 steps, explicit pass criteria; untrusted code is never executed on the radar host | Test plan: execution status NOT EXECUTED, baseline Huggingface (version to be asked), CUDA, Llama, tokens/sec and requests/sec, workload grid, 7-step procedure, pass criteria (≥ 5×), sandbox requirements | It's highly testable, so there's a test plan: baseline, hardware, metrics, workloads, procedure and pass criteria. It is generated and never run here. |
| 6 | 1:10 | `/diligence/unsloth-ai` | Open-source health · Risk: HIGH — 61% of commits come from one contributor (danielhanchen) | The Open-source health card: **Risk: HIGH**, known / unknown, evidence, the rating rule, agent assessment next to "Awaiting human review" | Underwriting has no overall score. Each category has its own rating and names its scale. Open-source health is a risk rating, and it's HIGH. |
| 6 | 1:19 | same | Technical differentiation · Evidence: MEDIUM — Native/kernel code is public (15 files), but none of the 4 performance claim(s) built on it is independently supported. | The Technical differentiation card: **Evidence: MEDIUM**, 5 known, 4 unknown, kernel paths cited | Technical differentiation is rated on a different scale, evidence strength, and it's MEDIUM. The kernel code is public, but none of the four speed claims is independently supported. |
| 7 | 1:29 | same, *Questions for the founders* | 6 questions (from claim 11) — why: Claim 11 (open source) holds, but the licence is not uniform across the repository. | Question 01: "unslothai/unsloth is Apache-2.0 at the root, but part of it ships under AGPL-3.0 (studio/LICENSE.AGPL-3.0). Which components does a commercial deployment need…?" with *why this question* and its source | Six questions for the founders, and each one says why it's asked. The critical one: the root licence is Apache-2.0, but `studio/` ships under AGPL. Which parts does a commercial deployment need? |
| 8 | 1:38 | `/agents` | 8 agents evaluated · DILIGENCE ANALYST: 7 runs, citation validity 100%, unsupported claims 0% · regression 16/16 passing | Per-agent table; missing values are "—" with numbered reasons, never zeros | Can we trust the agents? Every output is evaluated. Where a number can't be computed, it shows a dash and says why. |
| 8 | 1:50 | `/agents/failures/0011` | FAILURE 0011 · injection guard · found by the automatic evaluator — Meeting brief text "Founder Score 48/100" flagged as a prompt-injection attempt… · fixed | The failure record: claim, evidence ("Found by the agent evaluator…"), category TOOL FAILURE, HIGH, resolution, regression case and test (`tests/unit/test_llm_guard.py::test_reported_scores_are_not_injection`), origin: Automatic evaluator | The evaluator caught a real failure. The injection guard flagged our own "Founder Score 48/100" as a prompt injection. It's fixed, and a test pins the fix. |
| 8 | 2:01 | `/agents/cases#case-injection-false-positive-001` (opened by clicking the regression case) | injection-false-positive-001 — Reported scores in Radar's own briefs are not prompt injection · latest run: PASS | The regression case row: MEETING BRIEF / injection_detector, TOOL FAILURE, **PASS** on the last run, linked back to FAILURE 0011 | It's now a regression case, and it passes. All 16 cases pass. |
| 9 | 2:08 | closing card | — | RADAR FINDS IT. DILIGENCE TESTS IT. PEOPLE DECIDE. with three counts: 12 material claims, 6 founder questions, 18 documented failures pinned by a regression test | Radar finds it. Diligence tests it. People decide. |

The take ends at 2:16.

Re-record with `TOUR=diligence BASE_URL=http://localhost:3010 e2e/record-demo.sh`. Change the pacing per scene with `DEMO_DWELL="claim=12,agents-1=10"`, or all at once with `DEMO_PACE=1.1`. For a fast functional check with no video, run `npx playwright test -c playwright.config.ts diligence-tour.spec.ts` from `e2e/`.
