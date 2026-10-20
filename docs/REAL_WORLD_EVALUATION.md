# Real-world evaluation (M7)

**Date:** 2026-10-20 (data clock `as_of` 2026-10-20). **Data:** the live sweep of Oct 20 plus the verified talent
pool, which is people with verified commits to frontier-lab repositories, stored in
`Person.attrs['lab_contributions']`. **Mode:** `LLM_PROVIDER=none`, so no LLM ran anywhere in this evaluation.
Rankings were recomputed locally (`radar sweep --only score --only meet`, then `POST /api/meet/refresh`). Every
number marked "verified" below was checked against the live GitHub API (`api.github.com`, authenticated) or the
public URL on 2026-10-20.

This document is deliberately unflattering. Where something looks interesting but is unproven, it says so.

## 1. What was processed (dev DB `radar`, no demo rows)

| Entity | Count | Notes |
|---|---:|---|
| Companies / organizations | 200 | 171 `project`, 27 `company`, 2 `lab`. 168 have no creation date (names taken from text with no repositories or people), so they cannot be judged "young". |
| People | 2,951 | 1,421 topic-classified. **450 in the verified talent pool** (lab-repository commits). |
| Repositories | 303 | |
| Research papers | 412 | |
| Signals | 1,113 | arXiv 511 · GitHub 318 · Hacker News 283 · website 1. **0 X**, **0 job postings**. |
| Jobs | 0 | No ATS boards matched any formation candidate. |

## 2. What was surfaced

| Surface | Count |
|---|---|
| Formation candidates (stored) | **10**, all EARLY (6 team, 4 person). 0 EMERGING, 0 HIGH. The home strip shows 6, because it only counts candidates with activity inside the recent window. |
| Who should we meet (week 2026-W43) | **5**: 4 MEDIUM, 1 WATCH. 0 HIGH, because nobody has evidence from ≥ 2 source families in the last 90 days. |
| Strange signals | **1**: `shared_prior_employer_new_org`. Four people listing Peking University are contributing to `egosteer`, an org about 126 days old. egosteer itself falls just below the formation threshold (29.9). |
| Formation anchors rejected (with reasons) | company: 168 `created_unknown`, 12 `not_young`, 3 `curated_list`, 1 `off_thesis`, 1 `no_traction`, 2 `below_threshold`. person: 7 `new_project_not_counted`, 3 `formation_public`, 1 `generic_founder_bio`, 4 `below_threshold`. |

Nothing reaches EMERGING. That is the honest outcome, not a bug: EMERGING needs ≥ 2 independent source families.
On this data almost every candidate is GitHub-only:

- no X data
- no job postings
- arXiv author linking does not attach papers to the talent-pool people

The four multi-family teams (DeepLethe, robocurve, Maker's Pet, AetherLabs) all score below 50.

## 3. Formation Radar: top 10 (after)

The "Verified" column reports what we confirmed on GitHub on Oct 20.

| # | Candidate | Score / tier | Why surfaced | Verified | Genuinely interesting? |
|---|---|---|---|---|---|
| 1 | **Kevin Zakka** (person) | 51.1 EARLY, POSSIBLE FORMATION | 149 commits to google-deepmind/mujoco_menagerie and 123 to google-deepmind/mujoco. New repo [kevinzakka/mjbatch](https://github.com/kevinzakka/mjbatch) ("running thousands of MuJoCo simulations in parallel on CPU"): 600★, 60 forks within 40 days. | ✔ commit counts 149 / 123 (`commits?author=`); ✔ mjbatch 600★ / 60 forks, created 2026-09-10; profile bio "PhD @ UC Berkeley." | **Yes, as talent.** A strong robotics-simulation engineer with a fast-growing new library. Nothing yet says *company*. A meeting is defensible; "formation" is unproven. |
| 2 | **smg-project** (team: Yineng Zhang, Roger Wang) | 50.8 EARLY, FORMATION SIGNALS DETECTED | GitHub org created 2026-06-04 (138 days). Two members with verified lab history: zhyncs, 784 commits to sgl-project/sglang; ywang96, 214 to vllm-project/vllm. Convergence: *"Two members previously contributed to vllm-project/vllm and sgl-project/sglang (verified commits) — proven engineers from separate projects converging on smg-project, not prior shared history"*. Founder Quality 17.3 / 25. | ✔ org created 2026-06-04T21:39Z; ✔ 784 / 214 commits; ✔ both are public members; ✔ smg-project/smg 652★ / 198 forks, homepage lightseek.org/smg. | **Partly.** See false positive 3: our "team" is the 2 tracked people, who made 6 and 3 commits. The repo's real core contributors are slin1237 (1,255 commits), hello-alexmcc (981) and CatherineSue (403), and none of them are tracked. It has 159 contributors and looks like an open-source gateway project that moved into its own org. zhyncs's bio "LightSeek Mafia" plus the lightseek.org homepage are worth a human look. |
| 3 | **Zhengyi "Zen" Luo** (person) | 44.0 EARLY | 57 commits to NVlabs/GR00T-WholeBodyControl. New repo [ZhengyiLuo/AgentsDock](https://github.com/ZhengyiLuo/AgentsDock): 99★, 11 forks in 39 days. | ✔ 57 commits; ✔ 99★ / 11 forks, created 2026-09-11. The bio says "currently a Researcher at Nvidia". | **Weak.** He is a strong researcher, but AgentsDock has no description, and his bio says he is still at NVIDIA (the bio is not parsed into a current company; see limitations). Most likely a side project. |
| 4 | **Daoyuan Li** (person) | 40.3 EARLY | 27 commits to huggingface/trl. Two new repos with traction: [mini-verl](https://github.com/DaoyuanLi2816/mini-verl) (307★ / 75 forks) and [RepoGuardBench](https://github.com/DaoyuanLi2816/RepoGuardBench) (73★ / 14 forks). | ✔ 27 commits; ✔ 307★ / 75 and 73★ / 14. | **Talent, not formation.** mini-verl is an educational single-GPU port of verl. |
| 5 | **AetherLabs-AI** (team) | 39.2 EARLY | Org created 2026-09-09. Its website says *"Our founding team are leading experts in causal discovery…"*. Video2World is at 182★. Two contributors. | ✔ org created 2026-09-09; ✔ the phrase is on aetherlabs.ai; ✔ Video2World 182★ (2 contributors). | **Maybe.** It looks like a real new company (causal AI) with a website and formation language. The members are unknown (Founder Scores 21 and 6), so it is a cheap check rather than a meeting. |
| 6 | **Omar Rayyan** (person) | 37.4 EARLY | 21 commits to google-deepmind/mujoco_menagerie. New repo [FetchMan](https://github.com/omarrayyann/FetchMan) (humanoid loco-manipulation), 67★. | ✔ 21 commits; ✔ 67★, created 2026-08-16. | **Weak.** Research code from a robot-learning student. The traction is modest. |
| 7 | **DeepLethe** (team, 12 members) | 35.5 EARLY | Org created 2026-05-08. [deeplethe/utopia](https://github.com/deeplethe/utopia): +1,166★ in 30 days, 19 new contributors, 9 releases in 90 days. Evidence comes from 3 families (GitHub, HN, arXiv). | ✔ org created 2026-05-08; ✔ utopia is now 8,460★ / 1,192 forks with 31 contributors; ✔ release v0.1.0-rc9 on 2026-10-11. | **Interesting project, unknown team.** The "members" are community contributors with Founder Scores of 30 or below. The radar cannot tell who the founders are. |
| 8 | **Maker's Pet** (team) | 33.1 EARLY | Org created 2026-07-08 ("Home robots for makers"). [makerspet/oomwoo](https://github.com/makerspet/oomwoo), an open-source robot vacuum: +1,145★ in 30 days. | ✔ org created 2026-07-08; ✔ oomwoo 12,203★ / 766 forks; ✔ makerspet.com responds. | **Yes, worth a look.** Large hardware traction in a young org (manufacturing/robotics). The members are unverified. |
| 9 | **robocurve** (team, 11 members) | 32.6 EARLY | Org created 2026-06-25 ("We measure and report frontier robotics capabilities to the public"). [inspect-robots](https://github.com/robocurve/inspect-robots) +121★ in 30 days, 20 releases in 90 days. | ✔ org created 2026-06-25; ✔ 693★, 29 contributors. | **Probably an evals collective or nonprofit**, not a company. It is an honest EARLY signal. |
| 10 | **Tangency Labs** (team) | 30.8 EARLY | Org created 2026-10-16 (4 days old). [fieldcraft](https://github.com/tangency-labs/fieldcraft) is a Rust structures/heat/fluids modeler: +105★, released v0.7.0 on 2026-10-18. | ✔ org created 2026-10-16; ✔ 112★ / 14 forks; ✔ v0.7.0 published 2026-10-18. | **Yes, early.** A brand-new "Labs" org shipping engineering software (manufacturing/CAD-physics). The members are unknown. |

## 4. Who should we meet this week (2026-W43): before and after

**Before** is the stored week computed at 08:20 by the pre-M7 code. **Mid** is the state at the start of this pass,
after the partially committed M7 work in `c8bae5e`. **After** is the final state.

| # | Before (pre-M7) | Mid (partial M7) | **After** |
|---|---|---|---|
| 1 | Maya-Kid: WATCH 38.2, FS 28, "Affiliation unknown" (DeepLethe contributor) | kevinzakka 55.0 | **kevinzakka**: MEDIUM 55.0, FS 49 (mujoco, mjbatch 600★) |
| 2 | WaylandYang: WATCH 37.0, FS 30 (DeepLethe) | ZhengyiLuo 50.1 | **ZhengyiLuo**: MEDIUM 50.1, FS 46 (GR00T, AgentsDock) |
| 3 | tangjzh: WATCH 33.8, FS 22 (AetherLabs) | ywang96 47.7 | **zhyncs**: MEDIUM 45.7, FS 47 (SGLang 784 commits, smg-project) |
| 4 | jyviko: WATCH 33.1, FS 17 (Tangency Labs) | zhyncs 45.8 | **ywang96**: MEDIUM 41.3, FS 50. Card says "inferact (self-reported)… contributing alongside, not necessarily forming it" |
| 5 | rachittshah: WATCH 32.0, FS 33 (7 hobby repos) | youkaichao 44.4 (Inferact co-founder) | **omarrayyann**: WATCH 36.8, FS 32 (mujoco_menagerie, FetchMan) |

Verified talent with recent, traction-backed formation events now leads. Unknown young-org contributors fell out
of the top 5: they get a −10 "unverified" term and are forced to WATCH. Both co-founders of an already-funded
company were penalized out of the list.

## 5. Formation Radar: before and after

| # | Before (pre-M7 code, same data) | **After** |
|---|---|---|
| 1 | AetherLabs-AI 39.0 | **Kevin Zakka** 51.1 |
| 2 | Pareton 36.6 | **smg-project** 50.8 |
| 3 | DeepLethe 35.9 | **Zhengyi Luo** 44.0 |
| 4 | Roger Wang (person) 35.3 | **Daoyuan Li** 40.3 |
| 5 | Yineng Zhang (person) 34.1 | **AetherLabs-AI** 39.2 |
| 6 | Maker's Pet 33.1 | **Omar Rayyan** 37.4 |
| 7 | robocurve 32.9 | **DeepLethe** 35.5 |
| 8 | youkaichao 32.7 | **Maker's Pet** 33.1 |
| 9 | Simon Mo 32.0 | **robocurve** 32.6 |
| 10 | Nicolò Lucchesi 31.4 (`vllm-force-merge-stats`, 0★) | **Tangency Labs** 30.8 |
| 11 | Tangency Labs 30.9 | |
| 12 | Martino Russi 30.2 (`g1_sim_to_real`, 2★) | |

The pre-M7 list had several problems:

- smg-project was not a team candidate, and no reason was recorded.
- Its two members appeared as separate weak person candidates.
- Kevin Zakka was missing.
- Two candidates rested on trivial or zero-traction repositories.

## 6. False positives found, and what happened to each

1. **youkaichao and Simon Mo** (formation #8/#9 before; meet #5 mid). Both bios say they co-founded **@Inferact**.
   Inferact announced a $150M seed at an $800M valuation on 2026-01-22 ([TechCrunch](https://techcrunch.com/2026/01/22/inference-startup-inferact-lands-150m-to-commercialize-vllm/)).
   That is a public, funded formation, so it is too obvious for Anti Fund. **Fixed in general terms:** a static
   founder self-description naming an organization whose GitHub org is older than 180 days is no longer formation
   evidence (`formation_public`), and in Meet it triggers the obviousness penalty. Org ages come from observed
   public memberships (Inferact: created 2025-10-20).
2. **Roger Wang (ywang96), still #4 in Meet.** Press coverage names him as an Inferact co-founder too, but his
   GitHub bio does not say so. Our data only knows two things: his company field is "inferact" (self-reported),
   and he is a public member of the Inferact org. **Mitigated:** a young-org event counts half when the person's
   current company is another established org (`current_org_elsewhere`), and the card says so explicitly. **Not
   fully fixed:** without funding data the system cannot know Inferact is a funded company. It remains in the top
   5 on verified pedigree alone.
3. **smg-project is a coverage artifact.** It is a real young org with real verified talent, but our 2 "members"
   are minor contributors (6 and 3 commits). The repo has 159 contributors and its top committers are untracked.
   The candidate is honest in what it claims, but it overstates "team convergence". **Not fixed:** this needs
   contributor-coverage data (`contributors_total` was not measured) and ingestion of the anchor's top
   contributors.
4. **Pareton** (formation #2 before). A Bittensor subnet (SN10), 5★, 3 forks, members with Founder Scores 9, 7
   and 4. It scored on org-created, website and working-team facts plus a large attention gap. **Fixed:** a young
   org with no verified member, no member with Founder Score ≥ 45, and < 25★ / < 5 forks is rejected as
   `no_traction`.
5. **Nicolò Lucchesi, Martino Russi and rachittshah's 7 hobby repos.** They were candidates or meet picks based on
   `vllm-force-merge-stats` (0★), `g1_sim_to_real` (2★) and seven 0–5★ repos (several `*-mcp`). **Fixed** by the
   new-project noise filter: trivial names or serial authors need traction, and without traction a repo needs
   thesis depth. Those repos are also no longer cited anywhere as evidence: not on the candidate, and not in the
   Meet WHY NOW / WHAT CHANGED sections.
6. **Niklas Muennighoff** (mid list). His only formation evidence was `prefix-sliding`, a 27★ research-code
   release. It still *counts*, but 27★ is not *notable* traction (≥ 50★ / ≥ 10 forks / ≥ 2 contributors), so it no
   longer adds a second evidence kind. He fell below EARLY.
7. **Unknown young-org contributors leading Meet** (before: Maya-Kid, WaylandYang, tangjzh, jyviko). **Fixed**
   by the rebalanced formula (see §7).
8. **Remaining weak entries, not fixed and flagged here:**
   - Zhengyi Luo (#3): the bio says he is currently at NVIDIA.
   - Daoyuan Li (#4): an educational repo.
   - robocurve (#9): probably an evals collective.

   Each is labelled EARLY / POSSIBLE FORMATION, never "founded".

## 7. Ranking changes made

The M7 ranking work was partially committed in `c8bae5e` and completed in this pass.

| Area | Change | Test |
|---|---|---|
| Meet formula (`services/meet.py` docstring) | Founder Score weight raised to 40, the largest term. Talent (verified lab strength × strength of their own formation event) is up to 17. Formation-candidate membership is only 12, so it cannot outweigh verified history. Collision is 9. | `test_weights_sum_to_100…`, `test_verified_talent_with_traction_outranks_unknown…` |
| Meet priority gate | HIGH/MEDIUM require at least one of: verified pedigree (lab commits or public tier-listed org membership, never self-reported), a formation candidate with ≥ 2 families, or Founder Score ≥ 45. Otherwise WATCH. | `test_priority_gate_requires…`, `test_unknown_single_family_contributor_is_watch…` |
| Meet "unverified" | −10 for someone with no verified pedigree, Founder Score < 45 and single-family evidence, unless they are in a ≥ 2-family candidate. | same |
| Meet event strength | A young-org event scales with the person's own commits (0.6–1). It is halved when their current company is another org older than 180 days. | `test_event_strength_by_own_commits…`, `test_young_org_member_employed_elsewhere…` |
| Meet obviousness | Adds "own profile says founder of an org older than 180 days (formation already public)". | `test_cofounder_of_an_already_public_company…` |
| Meet evidence hygiene | Non-counting new repositories are never shown under WHY NOW / WHAT CHANGED. | `test_noise_repositories_are_not_why_now…` |
| New-project noise (`scoring/extract.py`) | Traction is ≥ 25★, ≥ 5 forks or ≥ 2 other contributors. Trivial names (`*-stats`, dotfiles, `*-mcp`, experiments…) and serial authors (≥ 7 new repos) need traction. Otherwise a repo needs thesis depth (≥ 2 subcategories, strength 0.35). Strength is 0.5–1 by stars, so a 600★ repo is 1.0. *Notable* traction (≥ 50★ / ≥ 10 forks / ≥ 2 contributors) adds the `project_traction` evidence kind. | `test_new_project_noise_filter_and_traction`, `test_new_project_traction_must_be_notable…` |
| Formation rejections | Every anchor that is left out carries a reason (`compute(..., rejected=[])`, `explain()`, `python -m radar.scoring.formation --explain <org\|person>`). `run()` reports counts by reason. | `test_unclassified_young_org_of_verified_lab_contributors…` |
| Thesis gate on missing topics | An unclassified young org whose members have verified lab commits takes those labs' themes. Missing topics are not evidence of being off-thesis. | same (smg-project shape: 6 and 3 commits, no topics) |
| Unverified team floor | No verified member, best Founder Score < 45, and < 25★ / < 5 forks → `no_traction`. | `test_unverified_team_without_traction…`, `test_unverified_young_org_without_traction…` |
| Already-public formation | `public_founder_org`: a founder phrase followed by an `@org` (or the profile company) whose GitHub org is older than 180 days → `formation_public`. "Founding engineer" does not count. | `test_public_founder_org…`, `test_founder_of_an_already_public_company…` |
| Tiers | EMERGING needs ≥ 2 families; a single-family candidate is never above EARLY. | `test_single_family_candidate_is_never_above_early`, notable-traction test |

## 8. Known limitations (honest)

- **Single-day history.** Everything was first seen on Oct 20, so novelty is uniform (6 / 6 for everyone) and
  "what changed" is mostly creation dates. Star deltas exist for some repos only. Elsewhere a lifetime star rate
  stands in, and it is labelled as such.
- **No X.** The provider is disabled, so the social family never fires, and nobody can reach three families
  without a hiring or research signal.
- **GitHub stats 202.** The commit-activity endpoints returned 202 (still computing) for many repos, so
  `commits_4w` and contributor velocity are missing. smg-project, for example, has stars and releases only, and
  the card says "commits, contributors not measured".
- **arXiv author linking** is name-based. Papers rarely attach to talent-pool people, so the research family is
  rare. This is the main reason nothing reaches EMERGING.
- **No funding or press data.** "Already formed" is inferred only from org age and the person's own bio, which is
  why Roger Wang remains in the list.
- **Coverage.** Team membership reflects the people we track, not the anchor's full contributor base (see
  smg-project).
- **Bios are not parsed for the current employer** (e.g. "Researcher at Nvidia").
- **Commit counts** are default-branch commits from the contributors / commits APIs. Squash merges can
  under-count maintainers.
- **Verified commits ≠ employment.** All wording says "contributed N commits to …", never "worked at".
- **No job postings** were ingested for any formation anchor, so the hiring family is empty.

## 9. The public demo (verified snapshot) vs. this evaluation

The public site serves the **verified snapshot** in `backend/radar/demo/snapshot/` (Oct 20, 2026, captured in two passes). Replayed into an empty database with `radar seed-demo --reset --no-fictional`, it holds a subset of what the dev database above had accumulated over several sweeps that day: one capture per source and fewer repeated observations. Thresholds were **not** lowered for the demo, so the result is smaller:

| | Dev DB (sections 3–5) | Snapshot replay (public demo) |
|---|---|---|
| Formation candidates | 10 EARLY (6 team, 4 person) | 6 EARLY, all person: Sameh Khamis 44, Kevin Zakka 40, Simon Mo 39, Zhengyi Luo 34, Jun Penn 32, Daoyuan Li 30 |
| Teams | DeepLethe 35.5, Maker's Pet 33.1, … | none. DeepLethe 29.7 and Maker's Pet 28.4 fall just under EARLY (30): no member relationship predates the org, and member Founder Scores are low |
| Who to meet (2026-W43) | see section 4 | 5 people: Kevin Zakka, Zhengyi Luo, Simon Mo (self-reported: inferact), Simo Lin (sgl-project/sglang) and Misha Goin (vllm-project, self-reported) |

Reproduce with `python -m radar.scoring.formation --explain DeepLethe` against the demo database.
