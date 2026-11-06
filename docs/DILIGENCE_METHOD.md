# Technical Diligence — method

Method version `dil-2026.11-2` (`radar/diligence/__init__.py`).

Diligence answers seven questions about a company found by the radar:

1. What does it claim?
2. What evidence supports each claim?
3. What can be independently verified?
4. What can be tested?
5. What remains unknown?
6. Which technical risks matter?
7. What should an investor ask the founders?

It produces an **evidence assessment, not an investment recommendation**. Statuses are never chosen by a model. They come from the deterministic rules below. Unknown stays unknown.

## Architecture

| Stage | Module | Network | Deterministic |
|---|---|---|---|
| Collection | `collect.py` | yes (guarded) | no — the world changes |
| Capture / replay | `capture.py` | no | yes |
| Analysis | `analyze.py` → `extract`, `evidence`, `contradictions`, `rules`, `testplan`, `oss`, `deps`, `security`, `ai_systems`, `infra`, `underwriting`, `questions`, `report` | no | yes |
| Persistence | `run.py` | no | — |
| API | `api/routes/diligence.py`, `diligence/workspace.py` | no | yes (sorted output) |

**Collection** produces `ArtifactDraft`s, one for each public artifact. Each carries:
- a key
- a kind
- a publisher
- a url and title
- `fetched_at`, `http_status`, `content_hash`
- the text, capped at 40,000 characters
- structured data
- injection flags

Every request is written to the run trace (url, status, ms, bytes, error). Collection works within one time budget, 180 s by default. Stages run in this order: website, GitHub, registries, OSV, Hacker News, papers. A stage that does not fit in the budget is skipped and recorded, and the run is marked partial.

**Analysis** is a pure function of the artifacts, the company-facts snapshot, and `as_of`. `as_of` is the moment of collection, so time-relative checks give the same answer when a capture is replayed later. These checks are push recency, release cadence and dependency age.

A capture (`radar/demo/diligence/<slug>.json.gz`) contains:
- the artifacts
- the facts
- the trace
- `collected_at`

It is gzip-compressed JSON with sorted keys and a zero timestamp, so the same capture always produces the same bytes. `radar diligence replay` and `capture.replay_all()` re-run the analysis offline.

## What is collected

- **Company site:**
  - The homepage, plus up to 8 same-site pages. Pages are ranked by how likely they are to hold claims: benchmarks, docs, pricing, security, product, changelog, blog, about. At most two pages of each kind are kept.
  - robots.txt is respected, and only HTML is accepted.
  - Text is extracted one block per line, so headings and sentences stay separate.
  - The crawl follows links from the homepage only. A blog post the homepage does not link is not reached this way (Modal's blog index renders its post list client-side, so "NVIDIA Vera CPUs double throughput for Modal Sandboxes" was missed).
- **Portfolio handoff (seed URLs):** a run started from a portfolio MaterialEvent (`radar diligence run <slug> --from-event <id>`, `--seed-url URL` (repeatable), or `POST /api/diligence/{slug}/run` with `{"event_id": …}`) also fetches the event's `source_urls`.
  - Only URLs on the company's own registrable domain (its website or recorded domain) are fetched, at most 5, with the same SSRF guard, robots.txt check, HTML-only rule and size caps. A redirect off the domain is dropped; refusals are in the trace.
  - Seeded pages are company web pages (`data.seed = true`) and rank with benchmark and docs pages.
  - The run trace and the capture record `{"type": "handoff", "event_id", "seed_urls"}`. A live run from an event logs a `diligence_requested` step in the portfolio decision log against that event (a replay never does). The event must belong to the company (API: 404 `event_not_found`, 422 `event_not_for_company`).
- **GitHub:** up to 3 primary repositories by stars, or 1 without a token, because anonymous access allows 60 requests per hour. For each repository:
  - Repository metadata.
  - The raw README.
  - The language breakdown.
  - The recursive tree, as paths only, capped at 5,000.
  - Selected files, read as text through the API:
    - manifests
    - Dockerfiles and compose files
    - `.env.example`
    - SECURITY.md and LICENSE
    - up to 4 CI workflows
    - benchmark docs
  - Releases.
  - Contributors, including per-author weekly statistics when GitHub has computed them.
  - Weekly commit activity.
  - About 50 recently closed issues.
  - Merged pull requests in the last 30 and 90 days. This needs a token.
  - Published security advisories.

  Nothing is cloned, built or run.
- **Registries:**
  - PyPI versions and dates (from the release RSS), plus latest-version metadata.
  - pypistats recent downloads, for the company's own packages only.
  - npm metadata and download counts.

  Packages are looked up in three roles: the company's own, benchmark baselines named on company pages, and up to 15 runtime dependencies.
- **OSV.dev:** checked only for dependencies pinned to an exact version. Unpinned dependencies are listed as "not checked (unpinned)". A vulnerability is reported only with its OSV id.
- **Third parties:**
  - Hacker News stories and comments that name the product.
  - A "Show HN" or "Launch HN" naming the product counts as the company's own post, and so do its author's replies in that thread.
  - Papers already in the radar that link the company's code. A paper the radar attributes to the company counts as company work.

## Publishers

| Publisher | Label | Meaning |
|---|---|---|
| `company` | COMPANY STATEMENT | Written by the company: website, README and docs prose, its own launch posts. Never counts as support. |
| `code` | PUBLIC ARTIFACT | The company's public code and releases: source paths, manifests, CI configs, release tags, repository metadata. Verifiable, but not an independent opinion. |
| `registry` | REGISTRY METADATA | PyPI, npm, OSV. |
| `third_party` | INDEPENDENT SOURCE | Independent authors (HN, papers). |

README and docs prose are company statements, not code evidence. Indirect code support comes from source paths, manifests, CI configs, release tags and code files.

## Claims

The claim types are PERFORMANCE, TECHNICAL_CAPABILITY, ARCHITECTURE, ECONOMICS, PRODUCT, SECURITY, SCALABILITY, CUSTOMER_TRACTION, MARKET and OPEN_SOURCE. Their definitions are in `taxonomy.py` and are exposed by the API as `definitions`.

**Rule-based extraction** (`extract.py`) works only on company-published artifacts.

1. Text is split into sentences. Fenced and indented code, tables, badges, images and HTML are removed. A heading counts as a sentence.
2. Each sentence is classified by ordered cue patterns. The first type that matches wins. A TECHNICAL_CAPABILITY cue needs a named target, such as a GPU, OS, framework, model or cloud; "multi-cloud" without a supports-verb in a sentence that describes the stack (runtime, engine…) is ARCHITECTURE. A named organisation using the product ("… that Snorkel is evaluating") is CUSTOMER_TRACTION. Other people's open source ("open source frameworks like …", "open-weight models", "the open-source ecosystem") is not an OPEN_SOURCE claim.
3. **Materiality filter:**
   - 5–60 words
   - not boilerplate
   - not a question
   - concrete, meaning it contains a number, a named technology or a proper noun
   - quantitative claims ("double" counts as 2×), production-readiness claims and homepage sources rank higher; roadmap language ranks lower; architecture claims rank higher for each named component (runtime, storage, scheduler…)
   - **not a company claim** (`not_a_claim`; hand audit of the live Modal run, Nov 6, 2026):
     - table or price-list fragments (a third or more of the tokens carry no letters) and worked cost examples ("This job will cost …", "adding up to", "75 GPUs × 24 hrs × $3");
     - headings and UI labels (Title-Case words, at most one lowercase word, no figure) and navigation labels glued to a sentence: a short Title-Case label whose last word is repeated ("Modal Infrastructure Infrastructure rebuilt for AI…") is cut off, so the sentence starts at the repeat;
     - catalog lines ("X is now available on Y") and sentences about a third-party model or product: a model family as the subject, a vendor's possessive ("NVIDIA's …"), a vendor product with no supports-verb ("NVIDIA CUDA Compiler Driver nvcc"), or model-card text (parameter counts, "active per token", context windows) — unless the company, "we" or "our" appears in the first 8 words ("NVIDIA Vera CPUs double throughput for Modal Sandboxes" stays);
     - first-person anecdotes ("For example, when we wanted to …");
     - CUSTOMER_TRACTION with no figure and no named customer ("Trusted by the world's leading AI teams");
     - ECONOMICS that is financing, or a billing rule (billed, multiplier, list price, credits) without a value assertion (cheaper, save, less than, versus).
   - **pricing pages** publish prices: at most one ECONOMICS claim per pricing page, and only a value assertion; plan bullets shorter than 8 words are not claims.
   - **blog, changelog, about and customer pages** (and seeded pages) narrate: a sentence there must name the company or speak as "we"/"our".
4. **Near-duplicates are merged:**
   - when token Jaccard similarity is at least 0.6, one sentence contains the other, two claims of the same type state the same figure ("3x"; "double" = 2x), or two OPEN_SOURCE claims name the same project;
   - the strongest source wins, in this order: homepage, benchmark or docs page, other page, README, docs, Show HN;
   - every source is kept as company evidence.
5. The ledger is capped at 12 claims and at most 4 per type, in deterministic order.
6. Sentences flagged by `detect_injection` are never claims.

`source_quote` is always a verbatim substring of the artifact. For markdown sources it is the raw markdown; `text` is the cleaned sentence.

**Optional LLM extraction** (`extract_with_llm`, purpose `diligence.extract`) runs only when a live provider is passed in. Sources are wrapped with `wrap_source` under `SYSTEM_GUARD`. A proposed claim is kept only if its quote is a verbatim substring of the cited artifact; rejected claims are counted. The model never assigns a status. With `NullProvider` the step is skipped.

## Evidence matchers (`evidence.py`)

| Matcher | Evidence | Relation / strength |
|---|---|---|
| `license` | Named license vs the SPDX license GitHub reports | supports direct, **verifies**; mismatch contradicts direct |
| `open_source` | "open source" vs a public repo under an OSI license. A claim about a named project ("Spindle is open source") counts only that project's repository; with none collected it gets a gap, never another repo's licence | supports direct, **verifies**; a source-available license (BUSL, Elastic, SSPL) contradicts direct |
| `capability_paths` | Named targets (hardware, OS, framework, model, scale) vs tree paths, using aliases such as H100 → `sm90`/`hopper` and MI300X → `gfx942` | supports **direct** if a source file names it, otherwise indirect; never verifies |
| `capability_config` | Target named in manifests, Dockerfiles, CI configs | supports indirect |
| `capability_release` | Target named in release tags | supports indirect |
| gap | A named target with no code evidence | explicit "no public evidence" marker |
| `languages` | "written in X" vs the GitHub language share | ≥25% verifies; 2–25% supports indirect; <2% contradicts direct |
| `public_metric` | Claimed stars, forks, contributors or downloads per period vs measured values (with the measurement date) | measured ≥ claimed verifies; measured < 0.8 × claimed contradicts direct; in between supports indirect. The contributor list is capped at 100, so a capped count never contradicts. |
| `release_maturity` | "production ready / battle-tested / GA" vs releases and the latest registry version | no release, only pre-releases, or pre-1.0: contradicts indirect (CLAIM REQUIRES CLARIFICATION); ≥1.0 stable: supports indirect |
| `maintenance` | "actively maintained" vs last push | ≤30 days verifies; ≥180 days contradicts direct; otherwise context |
| `cloud_footprint` | "multi-cloud / any cloud" vs cloud SDKs and deployment paths | one provider: contradicts indirect; two or more: supports indirect |
| `third_party_mention` | HN / papers naming the product and a key term of the claim | lexical cues give supports or contradicts, **always indirect**; uncertain gives context. For performance claims a cue counts only if the text uses performance words. |
| `benchmark_harness` | Benchmark scripts in the tree | context (reproducible in principle; does not confirm the figure) |

For performance claims, code that targets a platform is context only. It shows the code runs there; it says nothing about a speed figure.

## Status rules (`rules.py`)

The rules are applied in order, and the first match wins. The rule id is written into `status_reason`. Company statements never count as support, and `context` items never count either way.

- **R1** Any direct contradiction from code, registry or a third party → **CONTRADICTED** (displayed as **EVIDENCE CONFLICT**; never called deception).
- **R2** Any direct evidence that verifies the claim, from code, registry or a third party, with no contradiction of any kind and no uncovered aspect → **VERIFIED**.
- **R3** At least two supporting items from non-company publishers, on distinct artifacts, with no contradiction of any kind and no uncovered aspect → **SUPPORTED**.
- **R4** At least one non-company supporting item, but only one, or mixed with indirect conflicts (from any publisher, including the company's own conflicting statements) or with aspects that have no evidence → **PARTIALLY_SUPPORTED**.
- **R5** Testability NOT_TESTABLE and no non-company support → **UNTESTABLE**.
- **R6** Otherwise → **UNVERIFIED** (only the company's own statement exists). Pending clarifications are counted in the reason.

R2 is stricter than "no direct contradiction". A verified license does not make "fully open source" VERIFIED while the company's own pricing page describes a cloud-only component. Such a claim is PARTIALLY_SUPPORTED.

**Confidence:**

| Rule | Confidence |
|---|---|
| R1 | HIGH (code/registry) or MEDIUM (third party) |
| R2 | HIGH |
| R3 | HIGH with a direct support, otherwise MEDIUM |
| R4 | MEDIUM with a direct support, otherwise LOW |
| R5, R6 | LOW |

## Testability (`classify_testability`)

| Claim | Testability |
|---|---|
| Roadmap language ("coming soon", "will support") | NOT_TESTABLE |
| PERFORMANCE with a number (or "double", "twice", "triple") | HIGH; with a named baseline but no number: MEDIUM; otherwise LOW |
| TECHNICAL_CAPABILITY naming a target | HIGH; otherwise MEDIUM |
| OPEN_SOURCE | HIGH |
| CUSTOMER_TRACTION on public metrics (stars, downloads, contributors, forks) | HIGH; named references without counts: LOW; customer/user counts: NOT_TESTABLE |
| ARCHITECTURE "written/implemented in <language>" | HIGH; otherwise MEDIUM |
| SECURITY compliance (SOC 2, HIPAA, ISO 27001, GDPR, "certified") | NOT_TESTABLE; configuration claims: MEDIUM |
| SCALABILITY / ECONOMICS | MEDIUM with numbers, LOW without |
| MARKET superlatives | NOT_TESTABLE; otherwise LOW |
| PRODUCT | MEDIUM (LOW when it rests on private metrics) |

## Contradiction detectors (`contradictions.py`)

| Detector | Trigger | Label |
|---|---|---|
| `open_source_incomplete` | "fully / 100% / completely open source" while company pages mention enterprise-only, cloud-only, proprietary or closed-source components | CLAIM REQUIRES CLARIFICATION |
| `license_mismatch` | Direct license or open-source contradiction | EVIDENCE CONFLICT |
| `production_ready_no_stable_release` | Production-ready claim with no release, only pre-releases, or pre-1.0 | CLAIM REQUIRES CLARIFICATION |
| `multi_cloud_single_provider` | Multi-cloud claim, single cloud provider in code | CLAIM REQUIRES CLARIFICATION |
| `stale_benchmark_baseline` | A claim or benchmark page names a baseline and version (e.g. "vLLM 0.8"), and the registry shows a newer release that is a different major version, at least 2 minor versions ahead, or released at least 120 days later. Records both versions and both dates. | CLAIM REQUIRES CLARIFICATION |
| other direct contradictions | e.g. measured stars below the claim | EVIDENCE CONFLICT |

## Validation plans (`testplan.py`)

Every HIGH or MEDIUM testability claim gets a plan with these fields:
- hypothesis
- baseline and version guidance: the version the company named and the latest release, with its date
- candidate
- hardware and models: named in the claim, named on the source page (labelled as such), or explicitly "not stated by the company"
- targets
- metrics by facet: throughput, p50/p95 latency, TTFT, peak memory, accuracy, cost per 1M tokens, install success
- workloads: batch sizes 1, 8 and 32, three sequence-length mixes, a concurrency sweep
- procedure: pinned versions, identical precision, warm-up, at least 5 runs, median and IQR, logged commands
- pass criteria. For example, "3x faster" passes if the geometric-mean ratio is at least 3× under comparable conditions; between 0.8× and 1× of the claimed ratio it is partially supported; below 0.8× it is contradicted. "Double" and "triple" are 2× and 3×.
- the baseline is taken from the claim, or from another quote of the same claim ("compared with an equivalent x86 GNR machine").
- general-compute claims (sandboxes, containers, CPUs, tasks, storage) get task workloads and metrics (completed tasks per minute across a concurrency sweep, per-task p50/p95, CPU throttling) instead of token workloads, and no model.
- sandbox requirements: disposable container, no host filesystem, no secrets, network only for pinned installs, resource limits, every command logged

The plan's `execution` is always `{status: "not_executed"}`. **Plans are never run on the radar host.**

## Open source, dependencies, security, infrastructure, AI systems

- **`oss.py`.** Open-source metrics, computed for the primary repository:
  - top-contributor share of commits
  - bus factor: the fewest contributors covering 50% of commits; bots excluded
  - active authors in the last 90 days
  - 12-week commit cadence: median, mean, and trend (last 6 weeks vs the 6 before, ±25%)
  - releases in the last 90 and 365 days, median release interval, latest release, whether it is ≥1.0
  - issue **time to close**: median of recently closed issues. This is never "response time": closing is observed, responding is not.
  - merged PRs in the last 30 and 90 days
  - license, archived status, forks, age, maturity
  - contributor growth from radar history

  Concentration risk:
  - Top contributor ≥60% is HIGH, worded as "71% of commits come from one contributor".
  - Top contributor ≥40% or a bus factor ≤2 is MEDIUM.

  A missing input is `None` with an "unknown — …" note.
- **`deps.py`.** Manifest parsing:
  - Manifests are parsed with regex, `tomllib` or `json`. `setup.py` is matched by regex and never imported or run.
  - Supported manifests: requirements files, pyproject (PEP 621, dependency groups, Poetry), setup.py and setup.cfg, package.json (runtime and dev listed separately), Cargo.toml, go.mod, Dockerfile, compose files and `.env.example`.
  - It classifies vendor SDKs and services: LLM providers, routers, agent frameworks, clouds, data stores, observability, evaluation.
  - It reports these risks:
    - deprecated or inactive packages
    - no release in more than 2 years
    - pinned to an older major version
    - git or URL installs
    - OSV vulnerabilities, each with its id
- **`security.py`.** Only text that was already fetched is inspected. It checks:
  - SECURITY.md presence and published advisories
  - possible secret patterns: AWS keys, private keys, `sk-…`, GitHub, Slack and Google keys, and high-entropy values named like secrets. These are reported as "possible secret pattern in <path> line N — verify" with the value **masked** (at most 4 characters shown).
  - example-config defaults: DEBUG on, default passwords, binding 0.0.0.0 without auth, `chmod 777`
  - supply chain: `curl | sh`, `trust_remote_code=True`, `torch.load` without `weights_only=True`, `pickle.load`
  - Dockerfiles: no USER (runs as root), `:latest` base images, `ADD <url>`

  Live systems are never scanned or probed.
- **`infra.py` / `ai_systems.py`.**
  - Infrastructure covers clouds, data stores, deployment artifacts, CI and observability.
  - The AI-system review runs only for AI-native companies. It covers:
    - model providers and concentration
    - switching
    - agent frameworks
    - evaluation evidence
    - LLM observability
    - fallbacks

  Each item is known, inferred or unknown, and the default is unknown.

## Technical underwriting (`underwriting.py`)

There is no single score. Each category has a rating on one of two scales:
- **Evidence scale**, STRONG / MEDIUM / WEAK / UNKNOWN: how well public evidence backs the company's position.
- **Risk scale**, LOW / MODERATE / HIGH / UNKNOWN: how much technical risk the evidence shows.

Missing inputs give UNKNOWN.

| Category | Scale | Rule |
|---|---|---|
| TECHNICAL_DIFFERENTIATION | evidence | STRONG: ≥3 capability/architecture/performance claims with direct code support; MEDIUM: 1–2; WEAK: code collected but none; UNKNOWN: no code tree |
| ARCHITECTURE | evidence | STRONG: tree + languages + parsed manifests + a supported architecture claim; MEDIUM: tree + languages + manifests; WEAK: part of these |
| CLAIM_VERIFICATION | evidence | STRONG: ≥60% VERIFIED/SUPPORTED and none CONTRADICTED; MEDIUM: ≥30% and ≤1 CONTRADICTED; WEAK otherwise |
| BENCHMARK_EVIDENCE | evidence | STRONG: a performance claim VERIFIED/SUPPORTED and a public harness; MEDIUM: a harness or a partially supported performance claim; WEAK otherwise; a stale baseline lowers the rating one level |
| SCALABILITY | evidence | STRONG: a scalability claim VERIFIED/SUPPORTED; MEDIUM: distributed-execution code; WEAK: claims without code |
| SECURITY | risk | HIGH: any HIGH finding; MODERATE: any MEDIUM finding or material question; LOW: reviewed, nothing MEDIUM+ |
| OPEN_SOURCE_HEALTH | risk | HIGH: archived, top-1 ≥60%, or no commits in 12 weeks; MODERATE: bus factor ≤2, top-1 ≥40%, or no release in 365 days; LOW otherwise |
| DEPENDENCY_RISK | risk | HIGH: a HIGH/CRITICAL OSV vulnerability or a deprecated runtime dependency; MODERATE: any OSV vulnerability, unmaintained runtime dependency, or git/URL install; LOW otherwise. Unpinned ranges are reported, not rated — libraries legitimately use ranges. |
| INFRASTRUCTURE | risk | HIGH: multi-cloud claimed but a single provider in code; MODERATE: no CI or no deployment artifacts; LOW: both present |
| AI_SYSTEMS (AI-native only) | risk | HIGH: one hosted model provider and no switching evidence; MODERATE: no evaluation evidence; LOW: switching/multi-provider (or self-hosted) and evaluation evidence |

## Questions for founders (`questions.py`)

Questions come only from evidence gaps and conflicts. Each names the claim, artifact or measurement behind it and carries a reason. For example: "Your benchmark shows 3x against vLLM 0.8; vLLM 0.11.2 shipped on 2026-09-12. Have you rerun it, and what changed?"

They are ordered by materiality:
1. conflicts
2. material security findings (possible live credentials, high-severity advisories)
3. high-severity dependency vulnerabilities
4. untested performance figures (asking for the exact benchmark configuration)
5. single-maintainer concentration
6. traction that rests only on the company's word
7. deprecated dependencies
8. single model-provider dependency
9. no evaluation evidence

There are no generic questions, and at most 10.

## Report and review

The `ResearchReport` has kind `diligence`, and its body is a `DiligenceReport` (pydantic, `extra="forbid"`) with these sections:
- company and date
- method version
- executive summary, built from counts:
  - artifacts reviewed
  - material claims
  - counts by status
  - strongest evidence
  - largest uncertainty
  - critical question
- claims
- technical differentiation, architecture, open-source health, security, dependencies, infrastructure, AI systems
- benchmarks
- underwriting
- contradictory evidence
- key risks
- unknowns
- questions for founders
- sources `S1…`

The markdown rendering is deterministic.

**Run status:**
- NOT_RUN
- IN_PROGRESS: a running row younger than its budget plus 2 minutes
- COMPLETE
- NEEDS_REVIEW: the latest complete run has a CONTRADICTED claim or a HIGH-rated risk category, and no `ReviewDecision` has been recorded for it

Review targets:
- a claim is reviewed by its `DiligenceClaim.id`
- an underwriting category by `"<run_id>:<KEY>"`

A re-run starts with no reviews, because new evidence needs a new judgement.

## Safety model

- **Untrusted input.** Fetched content is data. It is parsed, quoted and length-capped, never executed, imported or cloned. A test parses the package and fails on any subprocess, `os.system`/`popen`/`exec*`, `eval`/`exec`/`compile`/`__import__`, importlib, pickle or `shell=` call.
- **SSRF.** Every URL is checked before it is requested:
  - it goes through `sanitize_url`;
  - IP literals are checked offline;
  - hostnames are DNS-checked and must resolve to public addresses;
  - the guarded HTTP client re-validates at connect time and on every redirect.

  Private, loopback, link-local, CGNAT and metadata addresses are refused and recorded in the trace. Discovered links are followed only on the company's own site. Names taken from fetched text are validated against strict patterns and URL-quoted before going into fixed-host API URLs. The only POST target is the constant OSV endpoint.
- **Prompt injection.** Artifacts are scanned with `detect_injection` and the hits are stored in `flags`. Flagged sentences are never extracted as claims. Statuses are rules over evidence, so injected text cannot change them; a test checks that removing an injected README sentence leaves every status unchanged.
- **Secrets.** Values are masked in findings (at most 4 characters shown). The fetched file text is kept in `diligence_artifacts.text` for audit, but the API never returns artifact text.
- **Caps.**
  - artifact text: 40k characters
  - response bodies: 400 KB per repository file, 2 MB per page, 3 MB otherwise
  - tree: 5,000 paths
  - evidence: 25 items per claim
  - API: artifact text is never returned
- **Budget.** One wall-clock deadline for each collection.

## Limitations

- **Synchronous runs.** `POST /api/diligence/{company}/run` runs synchronously within its budget (≤ 180 s). A hosting proxy with a shorter timeout will cut the HTTP response even though the run completes; long runs belong in a job queue.
- **Lexical matching.** Extraction and matching are lexical. A paraphrased claim with no cue word is missed, and a cue word can occasionally misclassify. The optional LLM pass can add claims but never statuses.
- **Code evidence.** It shows the code *targets* a platform, not that it works well there. Capability claims therefore top out at SUPPORTED, unless a verifying artifact exists (license, language share, measured metric, push recency).
- **Third-party evidence.** It is sparse and judged only by simple cue words. It is never direct, so it can support or question a claim, but it cannot decide it alone.
- **Partial coverage.**
  - Only the first 5,000 tree paths and up to 25 files per repository are read.
  - Secrets in unfetched files are not seen.
  - Private repositories, customer data and production systems are out of scope.
- **GitHub without a token.** Only one repository is covered, and there are no merged-PR counts. The contributor statistics endpoints can still be computing (HTTP 202), in which case activity per author is unknown.
- **Registry metadata.** Package ownership is not proven, and download counts exclude mirrors. PyPI history comes from the release feed (the 40–60 most recent releases).
