# V4 ready: Portfolio Pulse

## The numbers

| | |
|---|---|
| **Live URL** | https://anti-radar-one.vercel.app |
| **Portfolio URL** | https://anti-radar-one.vercel.app/portfolio |
| **Portfolio Brief URL** | https://anti-radar-one.vercel.app/portfolio/brief |
| **Demo URL** | Video: https://anti-radar-one.vercel.app/video#portfolio (2:31, captioned, silent). Guided tour: https://anti-radar-one.vercel.app/demo/portfolio (9 scenes) |
| **Snapshot date** | **Nov 6, 2026**. Live public capture `backend/radar/demo/snapshot_updates/2026-11-06/`, on top of the Oct 20 and Nov 5 snapshots. |
| **Public portfolio companies monitored** | 16 selected publicly disclosed Anti Fund portfolio companies: the technical companies with enough public footprint, out of the 53 that antifund.com lists (checked Nov 6, 2026). Plus 43 comps (46 comp relationships). This is a selection, not the whole portfolio. |
| **Raw changes detected** | 1,215 in the 30-day window, Oct 7 – Nov 6 |
| **Candidate events** | 432, then 283 after deduplication |
| **Material events surfaced** | 6 MATERIAL and 35 WATCH. 29 WATCH events reach a portfolio company; 6 belong to non-direct comps and reach none. |
| **Noise events filtered** | 242 (counted and shown, not hidden) |
| **Companies needing attention** | 5, all MEDIUM priority (see below) |
| **Comps tracked** | 43 companies, 46 typed relationships. Each has a reason it is comparable, a public source and a confidence. |
| **Test totals** | 1,421 backend (955 unit, 441 integration on real Postgres, 25 eval regression) and 215 frontend. Playwright tours pass for `/demo`, `/demo/diligence` and `/demo/portfolio`. The public verifier passes on the snapshot build with no backend. |
| **Agent eval status** | PORTFOLIO ANALYST is registered in the same control plane as every other agent. All 24/24 YAML regression cases pass. The failure ledger has 44 documented real failures; 42 are pinned by a regression test. |
| **CI status** | Green on `main` (commit 8f6ed19). It covers backend lint, the full Postgres suite, frontend typecheck, lint, tests and build, and a smoke test of the snapshot site with no backend. |

PORTFOLIO ANALYST metrics, from its stored outputs:

| Metric | Value |
|---|---|
| Success | 100% |
| Citation validity | 100% |
| Unsupported-claim rate | 0.6% |
| Source coverage | 99.7% |
| Fallback | 0% |
| Errors | 0% |
| Cost | $0 (deterministic templates) |

## What was surfaced (real, Nov 6, 2026)

**MATERIAL.** Every event's own source returns HTTP 200; so do all 93 distinct source URLs behind the MATERIAL and WATCH events.

| Company | Event | Rule |
|---|---|---|
| Modal | Crosses $1B annualized run rate and raises a $750M Series D (modal.com/blog) | M2 |
| ElevenLabs | Introducing SFX v3 (elevenlabs.io/blog) | M4 |
| Trajectory | Introducing the Trajectory Platform (trajectory.ai/field-notes) | M4 |
| Factory, Cognition's direct comp | Acquires Mentlio, Typedef and HumanLayer (factory.com/news) | M2, then M8 for Cognition |

**Needs Attention.**

| Company | Priority | Action | Why |
|---|---|---|---|
| Cognition | MEDIUM | UPDATE COMPETITIVE MAP | A direct competitor made 3 acquisitions; Cognition launched SWE-2 Flash; Corridor is joining Cognition |
| Modal | MEDIUM | RUN DILIGENCE | Its "NVIDIA Vera CPUs double throughput" post; the handoff opens `/diligence/modal`, where it is claim 01 with a generated test plan |
| ElevenLabs | MEDIUM | PREPARE FOUNDER QUESTIONS | A launch plus hiring acceleration |
| Helion | MEDIUM | MONITOR | Its $175M CHIPS letter of intent and its direct comp CFS's proposed $85M award. Both are WATCH, labeled "proposed, not finalized". |
| Trajectory | MEDIUM | REVIEW | Platform launch |

## How it was verified

**Hand audit.** We audited the top events and every Needs Attention card on the real capture and found real failures:
- 18 in portfolio monitoring;
- 6 in the Modal diligence run (the diligence handoff subject).

Each one was fixed, filed in the failure ledger (`/agents/failures`) and pinned by a regression test.

| Area | Failures found |
|---|---|
| Event classification | A proposed award called a material contract. A pricing guide read as a price change. Model-catalog listings and customer stories read as launches. A third-party Show HN credited as a company launch. A security product read as an incident. A Head-of hire and a residency read as launches. |
| Coverage | JS-rendered newsrooms yielding nothing |
| Entity resolution | Redirect domains creating duplicate companies |
| Missed changes | A beta, a sunset, an integration, a performance claim, an organisation joining a portfolio company |
| Diligence extraction | Pricing-table rows extracted as claims. Third-party model descriptions attributed to the company. The triggering post never collected. |

**Reproducibility.** A fresh reseed from the committed snapshot alone reproduces the same funnel and the same events.

**Public / private boundary.**
- The config loader rejects private keys at any depth, and the API serves positions only from an explicit list of public fields.
- The verifier checks the deployed payload: no `private_fields`, ownership, cost basis, marks or reserves.
- Stage and year are exactly as antifund.com states them. The round is UNKNOWN unless the site says otherwise.

**Production.**
- `node scripts/verify_public_demo.mjs` against production passes all 85 HTTP, asset and README-link checks, including every portfolio route, both new videos and the private-field check.
- Its browser journey (pulse → company → event → comps → brief → diligence) passed 103/103 against the identical snapshot build locally. The 3 failures in that run were README links to pages not yet deployed; they pass on production. Against production, the browser run is blocked on this machine by a TLS-intercepting proxy, which is a local network issue, not a site failure.

## CI

The CI smoke test now also renders `/changes`, `/diligence*`, `/agents*` and `/portfolio*` with no backend. CI passed on `main` (8f6ed19).

## Acceptance questions

| Question | Answer |
|---|---|
| Can the system monitor publicly disclosed portfolio companies? | Yes. It covers 16 selected companies from antifund.com (not the whole portfolio), with verified identifiers. Unverified look-alikes are deliberately excluded. |
| Can it distinguish material events from noise? | Yes: 1,215 raw changes became 6 material, 35 watch and 242 noise, by documented rules M1–M11 (no language model). |
| Can it explain why an event matters? | Yes. Each event shows its rule and reason, a "why it matters" line, and its impact on each affected company. |
| Can it identify which companies need attention? | Yes. Five companies are ranked deterministically, each with why it surfaced, a suggested action and questions. |
| Can it connect competitor events to portfolio companies? | Yes. Comp events reach portfolio companies through typed, sourced comps (M8); Factory's acquisitions surface Cognition. |
| Can it generate evidence-backed review questions? | Yes. Each question is tied to its event and source, and persisted with a status. |
| Can it hand off to V3 Diligence? | Yes. RUN DILIGENCE starts the existing diligence with the event's own sources, and records the handoff in the trace and the decision log. Modal's workspace was produced this way. |
| Does it use one Company entity as the source of truth? | Yes. Positions and comps reference `companies.id`. Sync matches by slug, domain or GitHub org, and the reseed creates no duplicates. |
| Does it clearly separate public information from private firm fields? | Yes. Private fields are schema-only and empty, never serialized, and the verifier checks this. |
| Does it avoid inventing confidential portfolio data? | Yes. Nothing is inferred about ownership, value or private metrics. Proxies are labeled as public proxies ("12 open technical roles", never headcount). |
| Does the morning brief tell me something useful in under 2 minutes? | Yes. It gives five sourced items (a $750M round, two launches, a competitor's acquisitions), then Needs Attention, then what was filtered. |
| Can every material event be traced to sources? | Yes. All 93 distinct source URLs behind MATERIAL and WATCH events return 200. |
| Are Portfolio agents evaluated by the same Agent Control Plane? | Yes. PORTFOLIO ANALYST appears in `/agents` with its metrics, 16 ledger entries and YAML cases. |
| Does the public demo work without a backend? | Yes. Vercel serves the bundled snapshot, and the verifier and CI render every portfolio page with no backend. |
| Would an investor plausibly open this each morning? | It is built for that: five things worth knowing, then what needs attention, then why. Every line is sourced and the noise is counted. |
| Would this reduce manual portfolio monitoring? | For public signals, yes. Hundreds of changes a month are filtered down to a handful of reviewed events and questions. It does not see private updates. |
| Would this help a small firm behave like a larger institution? | Yes. It provides a consistent review rhythm, a decision log (EVENT → REVIEW → QUESTION → ACTION), competitive maps, and a diligence handoff, run by one person with agents that are themselves measured. |

## Known limitations

- **Public data only.** Private company updates, metrics, rounds and board information are invisible.
- **Financing appears only if announced publicly.** There is no funding database.
- **Sparse seed-stage coverage.**
  - Several seed-stage companies (Efference, Westmag, Kela, Aeon, Enigma) have little public footprint and show no material change. That is a true result, not padding.
  - Saronic's newsroom renders client-side, has no sitemap, and yields no dated items.
- **No human reviews yet.** The public snapshot shows "Not reviewed". Review actions work only on a writable backend.
- **Short history.** "Since last review" uses dated windows (last 14 days vs the prior 14) and, where available, the previous capture. Observed deltas across captures start with this snapshot.
- **Comps are judgement** (INFERENCE, with a source and a confidence). No financial multiples are shown, because no reliable public financials are used.
- **The portfolio and diligence videos are silent and captioned.** The radar video (Oct 20) predates V3 and V4 data.
- **X and press attention remain unavailable** without API access.

## Final readiness status

V4 is complete and deployed. One system now answers:
- **RADAR:** who is becoming interesting?
- **FORMATION:** who may be starting something?
- **MEET:** who should we talk to?
- **DILIGENCE:** what should we believe?
- **PORTFOLIO:** what changed after we cared?
- **THESIS:** where is the market moving?
- **AGENTS:** can we trust the system doing the work?
- **REPORTS:** what have we learned?

It covers 16 selected publicly disclosed technical portfolio companies, with real sources throughout. No LP CRM, fund accounting, cap tables, board portal or recommendation engine was built.
