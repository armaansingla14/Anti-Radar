# ANTI RADAR

**Conviction Before Consensus.**

## Start here

Anti Radar is one system that finds technical founders early, tests what companies claim, monitors what changes after investment, and measures the agents doing the work. Every line on the site links to a public source.

1. **Watch the 2.5-minute Portfolio Pulse walkthrough:** [video](https://anti-radar-one.vercel.app/video#portfolio) or [guided tour](https://anti-radar-one.vercel.app/demo/portfolio).
2. **Open [Portfolio Pulse](https://anti-radar-one.vercel.app/portfolio).** It shows what materially changed across selected publicly disclosed Anti Fund portfolio companies, why each change matters, and what to review.
3. **Follow one event into Diligence.** Modal's ["double throughput" post](https://anti-radar-one.vercel.app/portfolio/modal) hands off to [its diligence workspace](https://anti-radar-one.vercel.app/diligence/modal), where it is claim 01, with a generated test plan.
4. **See how the agents are measured:** [agent metrics](https://anti-radar-one.vercel.app/agents) and the [failure ledger](https://anti-radar-one.vercel.app/agents/failures). It holds 44 real failures, each pinned by a regression test.

[Live radar](https://anti-radar-one.vercel.app) · [Portfolio brief](https://anti-radar-one.vercel.app/portfolio/brief) · [Diligence: Unsloth AI](https://anti-radar-one.vercel.app/diligence/unsloth-ai) · [GitHub](https://github.com/armaansingla14/Anti-Radar)

## Discover. Diligence. Monitor. Decide.

1. **Discover.** Radar finds technical founders and companies whose momentum is changing, from GitHub, research, hiring, the web and social signals, before conventional startup databases catch up.
2. **Diligence.** Diligence tests what they claim. It extracts each material technical claim, checks it against public code, registries and independent sources, and says plainly what is verified, what is only claimed and what nobody can know from the outside.
3. **Monitor.** Portfolio Pulse monitors what changes afterward. It watches selected publicly disclosed Anti Fund portfolio companies and their competitors, separates material changes from noise, explains why each one matters and hands technical questions back to Diligence.
4. **Decide.** People decide. Agents accelerate the work, but evidence, evaluation and human judgment stay visible.

### [Portfolio Pulse](https://anti-radar-one.vercel.app/portfolio) · [Live radar](https://anti-radar-one.vercel.app) · [Diligence](https://anti-radar-one.vercel.app/diligence) · [Agent evals](https://anti-radar-one.vercel.app/agents) · [Video](https://anti-radar-one.vercel.app/video#portfolio)

Also: [Portfolio brief](https://anti-radar-one.vercel.app/portfolio/brief) · [Portfolio tour](https://anti-radar-one.vercel.app/demo/portfolio) · [Diligence tour](https://anti-radar-one.vercel.app/demo/diligence) · [What changed since Oct 20](https://anti-radar-one.vercel.app/changes)

[![Portfolio Pulse on the verified snapshot of Nov 6, 2026](demo/screenshots/12-portfolio-pulse.png)](https://anti-radar-one.vercel.app/portfolio)

The public site serves a **verified snapshot**: real public data collected on **Oct 20**, **Nov 5** and **Nov 6, 2026**. It is bundled with the site, so it loads instantly and every number can be checked against its source. Anti Radar is an independent project built around Anti Fund's thesis; it is not affiliated with Anti Fund.

---

## Why I built this

Startup databases mostly describe companies that already exist: funding rounds, press, headcount. The earliest evidence that a technical company is forming shows up elsewhere: repositories, papers, research collaborations, technical releases, careers pages, hiring changes, and social activity.

Each of those is a weak signal on its own. Dated and combined, they can show technical momentum before consensus forms. Anti Radar collects them from official APIs, ties them to the people involved, scores them deterministically, and turns the result into a short list of people worth meeting this week, with the evidence for each one and an honest account of what it doesn't know.

## What it does

| | |
|---|---|
| **Formation Radar**: technical people and teams who may be converging before a company is announced. Labels stay honest: *possible formation*, never "founded". | ![Formation Radar](demo/screenshots/02-formation.png) |
| **Why now**: the dated changes that made an entity surface, each linked to its source and marked fact or inference. | ![Why now](demo/screenshots/03-why-now.png) |
| **Signal collision**: independent evidence from different source families (GitHub, research, hiring, web, community, social) landing within a short window. A single source is shown as exactly that. | *"1 independent signal, one source family (GitHub): not yet a collision."* |
| **People before companies**: verified technical history (commits to frontier-lab repositories), relationships and formation signals for each person. | ![Person profile](demo/screenshots/04-founder.png) |
| **Attention gap**: technical momentum against measured public attention. Unmeasured sources (X, press) are shown as unavailable, never counted as zero. | ![Attention gap](demo/screenshots/06-attention-gap.png) |
| **Who should we meet this week?**: sourcing turned into about five people to contact, each with why this person, why now, what changed, evidence and confidence. | ![Who should we meet](demo/screenshots/05-people-to-meet.png) |
| **Anti Brief**: a source-cited research summary and a meeting brief per person. Unknowns stay unknown instead of being filled in. | ![Anti Brief](demo/screenshots/07-anti-brief.png) |
| **Since last snapshot**: what changed between two verified snapshots. Covers new companies and people, new formations, score movers in both directions (only when the scores are comparable) and who entered or left the meeting list. Every caveat comes first. | ![Since last snapshot](demo/screenshots/08-since-snapshot.png) |
| **Technical Diligence**: a claim ledger for one company. Each claim carries its company, public-artifact and third-party evidence, and a status set by documented rules. Testable claims get a generated test plan. The workspace also covers technical underwriting and questions for the founders. | ![Technical diligence](demo/screenshots/09-diligence.png) |
| **One claim, audited**: the evidence for and against it, the rule that set its status, and a generated test plan (never executed). | ![A claim](demo/screenshots/10-claim.png) |
| **Portfolio Pulse**: what materially changed across selected publicly disclosed Anti Fund portfolio companies and their competitors. Each change carries its rule, why it matters, the questions to review and a suggested action, and the noise filtered out is counted. | ![Portfolio Pulse](demo/screenshots/12-portfolio-pulse.png) |
| **Portfolio brief**: five things worth knowing each morning, built deterministically from material events, each line sourced. | ![Portfolio brief](demo/screenshots/14-portfolio-brief.png) |
| **Agent evaluation**: per-agent metrics computed from stored outputs (unmeasured values say why), a failure ledger, and regression cases that pin each documented failure. | ![Agents](demo/screenshots/11-agents.png) |

## Real-world evaluation

Anti Radar was run against real public data on Oct 20, 2026, and its output was reviewed by hand, with every evidence link checked. ([Full evaluation](docs/REAL_WORLD_EVALUATION.md))

| Analyzed | |
|---|---:|
| Companies and organizations | 200 |
| People (450 with verified commits to frontier-lab repositories) | 2,951 |
| Repositories | 303 |
| Research papers | 412 |
| Signals | 1,113 |

Manual review found real false positives, for example:
- side projects of researchers who are still employed;
- people surfaced mainly for heavy contributions to famous open-source repositories;
- co-founders whose company was already public;
- a young GitHub org whose tracked "members" were not its real core team;
- candidates whose formation evidence was a single bio line or a small research repo.

These were used to change the ranking: verified talent first, a penalty for already-public formations, noise filters and recorded rejection reasons. Remaining weak cases are listed rather than hidden. No prediction accuracy is claimed: formation is inference from public signals, not proof.

## Technical Diligence

Radar finds a company; diligence tests what it says about itself. A run on a company works in two stages.

1. **Collect.** The run fetches public artifacts with an SSRF guard and size caps, and respects robots.txt: website and docs, GitHub repositories (metadata, trees, READMEs, manifests, Dockerfiles, CI), package registries, Hacker News, and papers. Artifacts are stored as data and **never executed**. A capture can be replayed offline, deterministically.
2. **Analyse offline.** The run then:
   - extracts the material claims (performance, capability, scalability, security, open source, economics…) with a verbatim quote and source;
   - matches each claim against the evidence;
   - sets its status with documented rules R1–R6;
   - generates test plans and contradiction checks.

The statuses are **VERIFIED · SUPPORTED · PARTIALLY SUPPORTED · EVIDENCE CONFLICT · UNVERIFIED · UNTESTABLE**. A conflict is "claim requires clarification", never an accusation. A company's own statements never verify its own claims.

Beyond the ledger, each workspace has:
- technical underwriting (known / inferred / unknown per category; no single score);
- open-source health, dependencies and public-code security checks (no vulnerability is claimed without advisory data);
- questions for the founders, each derived from an evidence gap;
- agent and human review side by side. The public snapshot shows "Awaiting human review"; no review is fabricated.

**First real run: Unsloth AI (Nov 5, 2026).** 99 public artifacts gave 12 material claims: 2 verified, 5 supported and 5 unverified. The 5 unverified are 4 performance claims and a sandbox-security claim; none was contradicted. Two findings stand out:
- **The critical founder question:** the repository is Apache-2.0, but `studio/` ships under AGPL-3.0.
- **Open-source health:** a HIGH risk, since 61% of commits come from one contributor.

A hand audit of this run found 9 real extraction and matching errors. Each one is filed in the failure ledger with a regression test. Method: [`docs/DILIGENCE_METHOD.md`](docs/DILIGENCE_METHOD.md). Release status: [`docs/V3_READY.md`](docs/V3_READY.md) (diligence) and [`docs/V4_READY.md`](docs/V4_READY.md) (portfolio). Tour script: [`demo/DILIGENCE_DEMO_SCRIPT.md`](demo/DILIGENCE_DEMO_SCRIPT.md).

## Agent evaluation

Each stored agent output is evaluated: task success, structured-output validity, citation validity, unsupported-claim rate, source coverage, latency, cost, fallback and errors. Metrics carry their sample size, and a value that cannot be computed is shown as missing with the reason, never estimated.

Failures go into a ledger with a 13-category taxonomy: 44 documented real failures (from the Oct 20 evaluation, the diligence audits of Unsloth AI and Modal, and the portfolio audit), each with its evidence, resolution and the regression test that guards it. The suite has 24 YAML regression cases (`radar eval`).

The evaluator found a real bug on its own. The injection guard was flagging meeting briefs that reported "Founder Score 48/100" as prompt injection.

Model comparison is opt-in (`EVAL_PROVIDERS`), and a model recommendation is only shown when there is enough measured data. Details: [`docs/AGENT_EVALUATION.md`](docs/AGENT_EVALUATION.md).

## Portfolio Pulse

Radar answers *who is becoming interesting*. Portfolio Pulse answers *what changed after we cared, and what deserves attention now?*

**What it monitors.** It covers 16 selected publicly disclosed Anti Fund portfolio companies, plus 43 comparable companies, each with a reason it is comparable and a public source. The 16 are the technical companies, with enough public footprint to monitor, out of the 53 that [antifund.com](https://antifund.com/) lists. This is a selection, not the whole portfolio.
- Stage and year are copied exactly as the site states them.
- Nothing private is stored or shown: no ownership, cost basis, marks or internal metrics. The schema has room for a future firm mode, but those fields stay empty in public mode.

**How a change surfaces.** Every raw change is collected from the radar's existing collectors (company newsrooms, careers boards, GitHub, Hacker News, papers) and from USAspending.gov federal awards. Each change is then:
- typed into one of nine event types;
- deduplicated across sources;
- classified **MATERIAL / WATCH / NOISE** by documented, deterministic rules. No language model sets materiality.

**Competitors count too.** A competitor's event reaches a portfolio company through its comp relationship. For example, a direct competitor's acquisitions put Cognition on Needs Attention.

**Needs Attention cards.** Each card says:
- why the company surfaced and what changed;
- what it could mean, labeled as inference;
- which questions to review;
- a suggested action: review, monitor, update the competitive map, run diligence, or prepare founder questions. A card never says "contact the founder".

**Handoff to Diligence.** RUN DILIGENCE starts the existing diligence workflow on the event's own sources. Modal's "double throughput" post became claim 01 of its workspace, with a generated test plan.

**First real run** (window Oct 7 – Nov 6, 2026): 1,215 raw changes → 432 candidate events → 283 after deduplication → **6 material · 35 watch · 242 noise** → 5 companies need attention.

A hand audit of that run found 18 real errors in classification, extraction and entity matching. Examples:
- a proposed $85M award called a material contract;
- a pricing guide read as a price change;
- model-catalog listings read as launches;
- JavaScript-rendered newsrooms yielding nothing.

Each error is fixed, filed in the failure ledger and pinned by a regression test. The PORTFOLIO ANALYST agent is evaluated by the same control plane as every other agent. Method: [`docs/PORTFOLIO_METHODOLOGY.md`](docs/PORTFOLIO_METHODOLOGY.md).

## Limitations

- **X coverage depends on API access.** Without a key, social attention is reported as unavailable, never as zero.
- **Formation is inference, not proof of company creation.** Most current candidates are honest *early signals*.
- **Hiring coverage varies.** Only companies with a public careers page or ATS board are covered; no formation candidate had job postings in this snapshot.
- **Public data has blind spots.** Private repositories, stealth hiring and funding are invisible. "Already formed" is inferred from org age and self-descriptions.
- **History is short.** Velocities strengthen as snapshots accumulate; until then they say *insufficient history*.
- **arXiv identity is name-based** and linked to GitHub only on corroborating evidence, so research history is under-counted.
- **Diligence reads public artifacts only.** It cannot see private code, customer results or internal benchmarks, so many claims honestly end UNVERIFIED. Test plans are generated, not run: untrusted code is never executed on the radar host. Two companies have a diligence workspace in the public snapshot: Unsloth AI, and Modal (opened from a portfolio event).
- **Snapshot comparisons are partial.** Of the companies scored on Oct 20, only those re-observed by Nov 5 are compared (47 of 220); the rest are listed as not re-observed rather than guessed.

## The demo

- **[Portfolio Pulse video](https://anti-radar-one.vercel.app/video#portfolio)** (2:31, captioned, silent), recorded on the Nov 6 snapshot. **[Portfolio tour](https://anti-radar-one.vercel.app/demo/portfolio)**: the same nine scenes in the app. Script: [`demo/PORTFOLIO_DEMO_SCRIPT.md`](demo/PORTFOLIO_DEMO_SCRIPT.md).
- **[Diligence video](https://anti-radar-one.vercel.app/video#diligence)** (2:15, captioned, silent), recorded on the Nov 5 snapshot. **[Diligence tour](https://anti-radar-one.vercel.app/demo/diligence)**: the same nine scenes in the app. Script: [`demo/DILIGENCE_DEMO_SCRIPT.md`](demo/DILIGENCE_DEMO_SCRIPT.md).
- **[Radar video](https://anti-radar-one.vercel.app/video#radar)** (3:13, narrated, captioned), recorded on the Oct 20 snapshot, so its numbers predate the later snapshots. Also attached to the [v1.0.0 release](https://github.com/armaansingla14/Anti-Radar/releases/tag/v1.0.0). Script: [`demo/DEMO_SCRIPT.md`](demo/DEMO_SCRIPT.md). Shot list: [`demo/DEMO_SHOT_LIST.md`](demo/DEMO_SHOT_LIST.md). **[Radar tour](https://anti-radar-one.vercel.app/demo)**: its ten scenes in the app. Press → to advance.
- **[The radar](https://anti-radar-one.vercel.app)**: every page, all 424 companies and 5,054 people in the snapshot, served without a backend. The snapshot is read-only, so actions that write (watch, feedback, reviews, new investigations, diligence runs) need a live backend.

## How it works

1. **Collect** — scheduled jobs pull public data from official APIs and websites (GitHub, arXiv, Hacker News, company sites and public job boards, X when an API key is configured). Every record keeps its source URL and observation time.
2. **Resolve** — deterministic entity resolution (domains, GitHub orgs, linked URLs). Uncertain matches become review candidates; nothing is merged silently. Same-name researchers are only merged on corroborating evidence.
3. **Relate** — a relationships table links people, organizations, repositories and papers: *co-authored with, contributed with, contributed to, member of, worked with (inferred)*. Every edge is VERIFIED or INFERRED and carries its evidence.
4. **Score** — deterministic, explainable scores. No language model sets a number.
5. **Surface** — the morning view, Formation Radar, the weekly meeting list, Daily Radar and a stored morning brief.
6. **Investigate** — on demand, the RADAR ANALYST agent writes a source-cited brief or meeting brief. External content is untrusted data, never instructions.
7. **Diligence** — for a company worth a closer look, the DILIGENCE ANALYST builds a claim ledger from public artifacts (`radar diligence run <company>`).
8. **Evaluate** — every agent output is scored, failures are filed with regression tests (`radar eval`).

## Formation Radar

Formation Radar looks for people and teams *before* there is a company announcement. Candidates come from two directions:

- **Young organizations** — GitHub orgs created in the last 180 days in thesis areas with a working team (≥ 2 real contributors), enriched with their website, job boards, Hacker News threads and papers that link to their code.
- **A verified talent pool** — people with *verified* commits to frontier-lab and frontier-infrastructure repositories (Google DeepMind, OpenAI, NVIDIA, Meta, Physical Intelligence, vLLM, SGLang, PyTorch, Tenstorrent and others; see [`config/talent_sources.yaml`](config/talent_sources.yaml)), watched for new repositories with real traction, new young-org memberships, and clusters of lab alumni converging on the same project.

The **Formation Score (0–100)** is separate from ANTI SCORE and fully deterministic:

| Component | Max | Evidence |
|---|---:|---|
| Founder quality | 25 | members' Founder Scores and verified pedigree |
| Team convergence | 25 | prior relationships that *predate* the new org (co-authorship, co-contribution with commit dates, shared public org membership) |
| Technical activity | 20 | commits, stars, contributors, releases of the anchor — "insufficient history" when unmeasured |
| Formation evidence | 20 | org/repo created, first job posting, new site, launch, formation language, affiliation change — each a distinct dated item |
| Novelty / attention gap | 10 | first detection recency and measured attention gap |

Tiers: **HIGH CONFIDENCE** (≥ 70, ≥ 3 independent source families, convergence ≥ medium), **EMERGING** (≥ 50, ≥ 2 families), **EARLY SIGNAL** (≥ 30). Language stays honest: *possible formation*, *emerging team*, *formation signals detected* — nobody is called a founder without a founder relation on record. Every rejected anchor is logged with a reason (`python -m radar.scoring.formation --explain <org>`).

## Signal collision

A single signal can be noise. Independent signals around the same entity in a short window are much more interesting:

```
w_i      = confidence × strength × e^(−age / 21 d)          (42-day window)
F_family = 1 − Π(1 − w_i)                                    noisy-OR: repeats from one source saturate
raw      = Σ F_family × (1 + 0.5 · (k − 1))                  k = independent families present
raw     ×= 1.2 if the families landed within 14 days
C        = 1 − e^(−raw / 2)
```

Families: GitHub · Research · Hiring · Web · Community · Social. Measurements dated at observation time (e.g. "+400 stars in 30 days") are excluded — they already drive technical momentum. Output is human-readable: *"4 independent sources within 17 days — HIGH."*

## Attention gap

Technical momentum is compared with **measured** public attention (Hacker News points and mentions — probed directly, not inferred from absence — total stars, X followers when available):

```
TECHNICAL MOMENTUM 97 · PUBLIC ATTENTION 26 · ATTENTION GAP +71     (ShengShu, Oct 20 snapshot)
```

Missing sources are listed ("social attention unavailable"), never imputed. If nothing was measured, no gap is awarded.

## Who should we meet?

Every week Radar picks about five **people**, not companies. The ranking favors verified technical history and a recent, traction-backed formation event over raw activity; unverified contributors are capped at WATCH; people whose company is already public or funded receive an obviousness penalty. Each pick shows why this person, why now, what changed, evidence count and confidence — and a one-click **meeting brief** (30-second context, what they've built, what changed, five questions worth asking, things to verify, sources). Citations are restricted to that person's own evidence.

## ANTI SCORE

ANTI SCORE (0–100) estimates how interesting a company is to Anti Fund *right now*. It supports the argument; it is not the argument — every page leads with **why now**.

| Component | Max | What it measures |
|---|---:|---|
| Technical momentum | 25 | percentiles of log-scaled 30-day velocity: stars, commits, forks, new contributors |
| Founder signal | 20 | Founder Scores of people with a founder relation |
| Acceleration | 20 | second derivative of stars/commits + signal collision |
| Thesis fit | 15 | theme match × stage preference × learned preference |
| Novelty | 10 | first detection + dissimilarity to what's already tracked |
| Attention gap | 10 | technical percentile − measured attention percentile |

Weights, thesis keywords, affiliation tiers and discovery queries live in [`config/anti_fund_thesis.yaml`](config/anti_fund_thesis.yaml). Analyst feedback (Interesting / Not interesting with reasons such as *too obvious, wrong thesis, strong founder*) adjusts bounded preference weights.

## Data sources

| Source | Access | What Radar uses |
|---|---|---|
| GitHub | REST API (token) | repos, star history, commits, contributors with dates, releases, issues/PRs, org creation, public org memberships, profiles (self-reported fields labeled as such) |
| arXiv | public API | papers, authors, code links |
| Hacker News | Algolia API | Show/Launch HN, discussions, direct attention probes |
| Websites & jobs | robots.txt-respecting crawl; Greenhouse / Lever / Ashby public APIs | positioning, formation language, hiring by technical role class |
| X | official API v2 only (`X_BEARER_TOKEN`) | discovery phrases, seed accounts. Without a key: **disabled**, plus manual paste-a-post ingestion via public oEmbed |

## System architecture

```mermaid
flowchart LR
    subgraph Sources
        GH[GitHub API]
        AX[arXiv API]
        HN[HN Algolia]
        WEB[Websites + ATS]
        X[X API v2 / oEmbed]
    end
    subgraph Jobs["Scheduled jobs (GitHub Actions cron)"]
        SW["radar sweep: hn · arxiv · github · orgs · talent · enrich · web · stats · probe · x · score · meet · brief"]
    end
    subgraph Core["Backend (FastAPI)"]
        P[Providers → normalized batches]
        R[Resolution + provenance]
        REL[Relationships]
        S[Scoring: ANTI · Founder · Formation · Collision · Attention]
        D[Detectors: strange signals · hiring]
        A[RADAR ANALYST · meeting briefs]
    end
    DB[(Postgres)]
    UI[Next.js terminal on Vercel]

    GH & AX & HN & WEB & X --> P
    SW --> P --> R --> DB
    DB --> REL --> DB
    DB --> S --> DB
    DB --> D
    DB --> A
    S & D & A --> UI
```

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router), TypeScript, Tailwind — server-side proxy keeps credentials off the client |
| API | FastAPI + SQLAlchemy 2 + Alembic |
| Database | Postgres (Neon in production) |
| Jobs | GitHub Actions cron calling `radar sweep`; `radar worker` (APScheduler) for local/Docker |
| LLM | provider abstraction (Anthropic first, OpenAI optional, none → deterministic templates); cached by content hash; daily spend cap |

## Trust & provenance

- **FACT / INFERENCE / ANALYSIS** are kept apart everywhere. Commit counts and dates are facts; a shared self-reported employer is inferred; model text is analysis.
- **VERIFIED / INFERRED / UNKNOWN** on every displayed fact. GitHub profile company and bio fields are labeled *self-reported*. Verified commits are never presented as employment.
- **Every claim links to its source** through a consistent source drawer; every entity shows **data age** per source family.
- **No fabricated history**: velocities say *insufficient history* when Radar hasn't observed enough; past scores are only compared like-for-like.
- **Automation is not people**: CI bots and AI coding agents (Claude, Copilot, Devin, Cursor…) are excluded from teams and rankings.
- **Prompt-injection defense**: scraped pages, READMEs and posts are wrapped as untrusted sources; citations are validated against what the agent actually saw.
- **The public site can't be polluted or run up a bill**: it serves a read-only verified snapshot with no backend and no LLM. On a live backend, anything that writes or spends (ingest, investigate, briefs, watchlist, feedback) requires an admin token.

## Run locally

```bash
cp .env.example .env            # everything optional; see comments
make db                         # Postgres in Docker
make migrate
cd backend && uv run radar seed-demo --reset   # load the committed verified snapshot
make api                        # FastAPI on :8000
make web                        # Next.js on :3000
make test                       # backend tests (real Postgres)
```

Or the whole stack: `make up` → http://localhost:3000. To serve the bundled snapshot with no backend at all: `cd frontend && DATA_SOURCE=snapshot npx next build && DATA_SOURCE=snapshot npx next start`.

Useful commands:

```bash
radar sweep                     # full live sweep (or --only github --only talent ...)
radar ingest github --repo owner/name
radar ingest url https://example.com
radar formation --explain <org> # why an anchor is or isn't a formation candidate
radar investigate "<name>"      # RADAR ANALYST → Anti Brief
radar status                    # provider health, volumes, LLM spend
radar demo capture              # collect a new verified snapshot
```

## Deployment

- **Cost:** $0/month on free tiers.
- **How it's served:** the public site runs on **Vercel** and serves the bundled verified snapshot (`DATA_SOURCE=snapshot`), so no page waits on a backend.
- **Live backend:** the FastAPI service is optional. It runs on **Render** with **Neon** Postgres and **GitHub Actions** for scheduled ingestion, deployed with `scripts/deploy.py --live`. In that mode the site reads live data and falls back to the snapshot if the backend doesn't answer.
- **Before sending the link:** `node scripts/verify_public_demo.mjs` checks the public deployment end to end.
- **Full instructions:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
