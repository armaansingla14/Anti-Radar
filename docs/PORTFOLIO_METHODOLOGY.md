# Portfolio Pulse: methodology

Portfolio Pulse answers one question: *what changed after we cared, and what deserves attention now?*

It runs over publicly disclosed portfolio companies (`config/anti_fund_portfolio.yaml`) and their comps. These are the same `Company` entities the rest of ANTI RADAR uses. The engine is deterministic and offline (`backend/radar/portfolio/`). No LLM classifies events, sets materiality or ranks companies.

## Pipeline

| Stage | Module | What it does |
|---|---|---|
| Sync | `config.py`, `sync.py` | Strict config validation. Positions and comps are matched to existing companies by slug, domain or GitHub org. A company is created only when nothing matches. |
| Collect | `candidates.py` | Every raw change in the review window (30 days to the run day), plus a 90-day horizon kept for novelty checks only. |
| Normalise | `normalize.py` | Each raw change becomes a typed candidate event: one of 9 types, a subtype, and FACT or INFERENCE. |
| Deduplicate | `dedup.py` | One event per real-world happening. Every source is kept. |
| Materiality | `materiality.py` | Rules M1–M11 assign MATERIAL, WATCH or NOISE, and record the rule and the reason. |
| Impact | `impact.py` | What an event means for each portfolio company (SELF or through a comp, rule M8). |
| Attention | `attention.py` | Needs Attention cards: priority, why surfaced, questions, suggested action. |
| Questions and log | `questions.py`, `decisions.py` | Persistent open questions and the decision log. |
| Metrics | `metrics.py` | Public-proxy metrics with comparable windows. |
| Reports | `reports.py` | PORTFOLIO ANALYST writes a `portfolio_review` per company and a daily `portfolio_brief`. |
| Orchestration | `run.py` | `run_portfolio(session, day)` runs every stage. It is idempotent and writes `PortfolioRun` with the funnel counts. |

It runs in three places:
- `radar portfolio run`;
- the seed's post-seed steps, after diligence replay and before evaluation;
- `radar demo capture --update --portfolio`, which collects the data.

## Sources

The radar's existing collectors are pointed at each portfolio company and comp:
- **Website.** News, blog and changelog pages are fetched first. Dated items come from JSON-LD, `<article>`/`<time>` markup and list cards. Feeds (RSS/Atom) are read only when the page declares them and they sit on the same registrable domain; a DOCTYPE or ENTITY is refused.
- **Careers and ATS board.** Only the board verified by hand in the config.
- **GitHub org.**
- **Hacker News.** A story counts only when it links to the company's domain or GitHub org. A name-only mention is never an event.
- **arXiv.** Only papers whose code points into the org.
- **USAspending.gov.** Federal awards, by exact recipient name after removing punctuation and legal suffixes. Matching is never fuzzy.

Many newsrooms are rendered by JavaScript frameworks (Next.js, Framer, Webflow). On those the index page is read for:
- visible dates next to exactly one same-site link, including `dd.mm.yyyy` only when a date on the page settles the day/month order;
- CMS `data-date` attributes;
- JSON-LD lists without urls (matched to their link text);
- embedded JSON page state (`__NEXT_DATA__`), which is parsed and never executed.

When the index still yields fewer than 5 dated items, posts are discovered from the site's sitemaps (robots.txt `Sitemap:` lines, else `/sitemap.xml`) under the newsroom section, plus the newsroom's own post links. Each post page (at most 20) gives its title and publication date (`article:published_time`, JSON-LD, `<time>`, or a visible date by the title). A sitemap `lastmod` is used only as a last resort, and is then labelled `date_basis=sitemap_lastmod`.

Every record of a portfolio crawl is keyed to the configured identity. A redirect target (cognition.ai → cognition.com) becomes an alternate key, and the domains the config verifies (website, newsroom, `alias_domains`) resolve to that company, so a redirect never creates a second company. A configured `slug` names the company when it is created.

Every fetch goes through the SSRF-guarded client with robots.txt. External text is treated as untrusted data: it is quoted, never followed, and instruction-like text is flagged.

## Event types and normalisation

There are 9 types:
- PRODUCT
- TECHNICAL
- TALENT
- COMMERCIAL
- FINANCING
- COMPETITIVE (the portfolio-side view of a comp's event; stored on the impact as its relation)
- REGULATORY
- RISK
- STRATEGIC

Signals with a known shape map directly:

| Raw change | Event |
|---|---|
| USAspending award | COMMERCIAL.government_contract (amount kept) |
| GitHub release | TECHNICAL.repository_release for a major or first release of a repository with 200 stars or more, or a minor release of the organisation's flagship (most-starred) repository with 1,000 stars or more that is not an SDK / client / CLI / cookbook / examples repository. Everything else (patches, pre-releases, SDK minors, small repositories) is NOISE. |
| New repository | TECHNICAL.open_source_release at 50 stars or more, otherwise NOISE |
| Paper | TECHNICAL.research_paper |
| Founder or leader affiliation change | TALENT.executive_join / executive_departure |
| Homepage whose title changed | STRATEGIC.repositioning |
| Any other page change | NOISE: minor copy update or routine docs |
| Pricing page change | PRODUCT.pricing_change |

A Show HN or Launch HN counts as the company's own only when the story URL points at the company's site or GitHub org. One that merely mentions the company is third-party discussion: public attention at 50 points or more, otherwise NOISE.

Company posts and Hacker News titles first go through context rules, which use the title, the URL, the post text and the company's name:

| Rule | Event | Matches |
|---|---|---|
| N1m | RISK.marketplace_security (WATCH) | A security word together with marketplace, extension, plugin or package |
| N8s | NOISE customer_story | Case studies and customer stories (by URL, "How X … with Company", "X cuts … with Company"). Counted in `customer_stories_30d`. |
| N6p | COMMERCIAL.proposed_award (WATCH) | Proposed, letter of intent, preliminary memorandum, selected for negotiation, would receive / would help, plus an award word, in the title or the post text |
| N0g | NOISE guide | guide, what is, how to, explained, what it costs, examples, use cases |
| N11c | NOISE catalog_addition | "\<third-party thing\> is now available / in preview on \<platform\>" when the thing is not the company's own. Never counted as a launch. |
| N17 | PRODUCT.product_sunset | sunset, deprecate, discontinue, shutting down |
| N11b | PRODUCT.product_beta (WATCH) | beta, preview, early access |
| N18 | NOISE program | Residency, fellowship, accelerator, program (not "program of record"), summit, conference, workshop. Never counted as a launch. |
| N8o | PRODUCT.capability_announcement | "\<Company\> deploys \<thing\>". A customer announcement needs "\<Customer\> deploys \<Company\>". |

Then the ordered title rules. The first match wins:

| Rule | Type | Subtype | Matches |
|---|---|---|---|
| N1 | RISK | security_incident, outage, lawsuit, recall | breach, outage, incident, lawsuit, recall, vulnerability |
| N2 | FINANCING | acquisition | acquires, acquired, acquisition |
| N3 | FINANCING | new_round | Series A–H, seed round, raises/raised + $ / funding |
| N4 | FINANCING | strategic_investment | strategic investment, invests in |
| N5 | REGULATORY | regulatory_milestone | FAA, FCC, NRC, FDA, export control, operating licence, regulat… |
| N6 | COMMERCIAL | government_contract | contract, award + an agency word |
| N7 | COMMERCIAL | partnership | partner, partnership, collaboration |
| N8 | COMMERCIAL | customer_announcement | deploys, selects, customer |
| N9 | TALENT | executive_join / executive_departure | joins, appoints / steps down + a leadership title |
| N11 | PRODUCT | product_launch | launch, introducing, announcing, now available, GA |
| N10 | TECHNICAL | benchmark_claim, performance_claim | benchmark, N× faster, state-of-the-art, SOTA; doubles / triples / N× throughput, speed or latency (routed to RUN DILIGENCE) |
| N12 | TECHNICAL | research_paper | paper, technical report |
| N13 | TECHNICAL | open_source_release | open-source |
| N14 | PRODUCT | integration, new_feature | integrates, integration; now supports |
| N15 | PRODUCT | pricing_change | new pricing, pricing update, price cut / increase / change, now costs |
| N16 | STRATEGIC | market_entry | expands to, new office |
| — | NOISE | generic_post | anything else |

A performance phrase anywhere in a title is kept as `performance_claim`, so a launch that claims "3× faster" is routed to RUN DILIGENCE.

### Hiring

Hiring candidates are built from job postings dated by the board's posting date. A posting with no board date counts only if it was first seen *after* the first crawl, so a first crawl never looks like a hiring burst. All hiring candidates are INFERENCE.

| Subtype | Condition |
|---|---|
| specialized_hiring_cluster | At least 5 new postings in one specialised role class within 14 days |
| new_role_class | A specialised class posted in the last 14 days with no earlier dated posting, when at least 5 earlier dated postings give a baseline |
| hiring_acceleration | At least 5 new technical postings, and at least twice the prior 14 days |
| role_closures | At least 5 technical postings closed in 14 days. The board does not say whether they were filled or withdrawn. |

## Deduplication

Two candidates about the same company merge when all of these hold:
- they are no more than 7 days apart;
- they have the same type, or one is a Hacker News story about the other;
- they share a URL (for example, an HN story linking to the company's post) or their titles have a token Jaccard of 0.5 or more.

Merging is transitive. The merged event:
- keeps every source;
- takes its title and type from the most authoritative member (company, then registry, then third party);
- is dated at the earliest member;
- records `raw_count` as the sum of the raw changes it contains.

NOISE never merges with a real event.

The dedup key is the subject, the subtype and the primary URL. For an event with no URL it is the subject, subtype and normalised title, plus the ISO week. Hiring clusters use the subject, subtype, role class and ISO week.

## Materiality rules

The first matching rule decides. M9 can then cap the result.

| Rule | Condition | Outcome | Example |
|---|---|---|---|
| M1 | RISK event (security incident, outage, lawsuit, recall) | MATERIAL ("risk signal", never "crisis"). A security issue in a third-party item on the company's marketplace gives WATCH. | The company publishes an incident report |
| M2 | FINANCING announced by the company or a primary source | MATERIAL | "Acme raises $40M Series B" on acme.com |
| M3 | Government contract of $1M or more; a company-announced partner or customer naming a tracked company or an agency | MATERIAL. A smaller or unpriced award, or an unnamed partner, gives WATCH. A proposed award (letter of intent) is WATCH until it is awarded. | DOE award of $2.4M |
| M4 | Launch or major release announced by the company, and the first in 90 days | MATERIAL. A repeat launch, new feature or pricing change gives WATCH. | "Introducing Gelgrip 2" |
| M5 | Founder, C-level, president or board join or departure from a verified source | MATERIAL. A VP / head-of hire, or an unverified change, gives WATCH. | "Jane Doe joins as CTO" on the company newsroom |
| M6 | At least 10 roles of one specialised class in 14 days, or a new class | MATERIAL. At least 5 roles, acceleration or closures give WATCH. Always INFERENCE. | 12 new ML research roles |
| M7 | Paper, open-source release, benchmark, release, or repo traction | WATCH. MATERIAL if it bears on a V3 diligence claim (3 or more shared distinctive terms besides the company's name, and the claim was not extracted from the event's own source) or traction is high (1,000 stars or more on a major or new repo). | Benchmark that restates a diligence performance claim |
| M8 | A comp's event (`impact.py`) | A DIRECT comp's MATERIAL event is MATERIAL for the company. Other comp types give WATCH. A DIRECT comp's WATCH event gives WATCH. | A direct competitor launches |
| M9 | Confidence below 0.5, or only third-party sources from one family | Capped at WATCH | A financing seen only on Hacker News |
| M10 | Generic post, copy edit, docs, routine release, small repo, hiring snapshot, activity metric, low-engagement discussion (under 50 HN points) | NOISE | Homepage text edit with an unchanged title |
| M11 | Other typed events (REGULATORY, STRATEGIC) | WATCH | Homepage repositioning |

## Noise and the funnel

Every raw change in the window is counted. Noise is filtered, not hidden. `PortfolioRun.counts` stores the real funnel:

1. raw changes
2. candidates
3. after deduplication
4. MATERIAL / WATCH / NOISE
5. companies surfaced

Per-company counts are stored as well. The Pulse page and the brief show the funnel as stored.

## Competitor impact

`CompRelationship` rows come from the config. Each has a type (DIRECT, ADJACENT, INFRASTRUCTURE, INCUMBENT, OPEN_SOURCE), a reason it is comparable, a public source URL and a confidence. A comp is always a judgement (INFERENCE).

The same comp can belong to several portfolio companies; it is one `Company` with several relationships.

Impact confidence is the event's confidence scaled by the comp's stated confidence (HIGH 1.0, MEDIUM 0.8, LOW 0.6). The impact reason quotes `why_comparable`. NOISE never propagates.

Every event in the API carries `affects`: the portfolio companies it reaches, as SELF or through a comp, with the comp type, the reason and the materiality for that company. The events API filter `type=COMPETITIVE` selects events that reach a portfolio company through a comp. COMPETITIVE is a relation, never a stored event type. `materiality` accepts a comma list.

## Needs Attention

```
score = 3 · material own + 2 · material via comps + min(watch, 5) + 3 if any risk
        + (independent source families − 1) + recency (2 if 7 days old or less, 1 if 14 or less)
```

Priority:
- **HIGH:** 2 or more material events including one of the company's own, or any risk signal.
- **MEDIUM:** 1 or more material events, or 3 or more to watch.
- **LOW:** everything else. LOW companies are not shown.

At most 5 cards are shown.

Each card has:
- **why surfaced:** counts, span and families;
- **what changed:** the event titles;
- **what this could mean:** a template keyed by the combination of event types, labelled INFERENCE;
- **questions:** templated per subtype, each tied to its event and source;
- **suggested action.**

The suggested action is the first rule that applies:

| Condition | Action |
|---|---|
| RISK | REVIEW |
| A DIRECT competitor's launch or financing | UPDATE COMPETITIVE MAP |
| A performance or benchmark claim, or an event that bears on a diligence claim | RUN DILIGENCE (the V3 workspace and `POST /api/diligence/{slug}/run`) |
| A hiring cluster | PREPARE FOUNDER QUESTIONS |
| Only WATCH events | MONITOR |
| Anything else | REVIEW |

The system never suggests contacting a founder.

## Comparable windows and proxies

Proxies are stored in `metric_snapshots` as `proxy_*` metrics with source `derived`. Each carries a `source_url`, a `confidence` and a `proxy_label` saying what it is not:

| Proxy | It is not |
|---|---|
| Open technical roles (a public hiring signal) | headcount |
| Contributors | engineering headcount |
| Federal award obligations | revenue |
| Customer stories published in the last 30 days | revenue, or a customer count |

Windows are dated by the event itself: posting, release, publication, story or award date.

| Metric | Window compared |
|---|---|
| New technical roles | last 14 days vs the prior 14 |
| Releases | last 30 days vs the prior 30 |
| Hacker News points | last 30 days vs the prior 30 |

Levels (open technical roles, federal awards) are also compared with the previous capture.

A pair is comparable only when both sides were observed. Otherwise it says "not compared" and gives the specific reason:
- no posting dates on the job board;
- no careers board configured;
- no GitHub org linked;
- USAspending not queried (no recipient configured);
- first observation (no earlier capture).

Movers list only comparable pairs.

The `commercial` freshness family is observed when USAspending was queried, even when no award matched. It is `not_applicable` for a company with no configured recipient, which is different from never observed.

## Review workflow and decision log

`PortfolioDecision` records EVENT → REVIEW → QUESTION → ACTION.

The system logs only its own steps: `detected`, `classified` (with the rule), `question_raised` and `comps_updated`. Each carries a dedup key so re-runs never duplicate a line.

Human steps are written only when a person acts, under their own name:
- `reviewed`, from `POST /api/portfolio/{slug}/review` (MARK_REVIEWED / NEEDS_FOLLOW_UP / MONITOR / NO_ACTION), which is also a `ReviewDecision`;
- `monitoring_changed`;
- question status changes;
- `diligence_requested`, when diligence is run on a portfolio company.

Writes sit behind the admin gate and are refused with `403 snapshot_read_only` on the public snapshot, which shows "Not reviewed".

The next review is suggested 30 days after the last one.

## Comps policy

Comps must be real companies or projects. Each needs a public `source_url` showing the overlap, usually the comp's own product page. Aim for 2–4 per company.

Comp identities follow the same verification as portfolio identities. A short or common name is never resolved by name alone.

## Private-field boundary

`PortfolioPosition.private_fields` exists only for a future firm mode. In public mode:
- the config loader refuses ownership, cost basis, marks, reserves, round participation, board, internal-owner, internal-notes, revenue and customer-count keys at any nesting level;
- sync raises before a non-empty value could be written;
- the API serialises positions from an allow-list (`PositionOut`) that has no private field;
- tests assert that no endpoint ever returns one.

Unknown public facts stay UNKNOWN. Stage and year are copied exactly as the public source states them.

## Public-data limitations

- **Sparse data.** Seed-stage companies often have little public activity. "No material change" is a true result, and it is never padded.
- **Financing.** A round is known only if the company or a primary source announces it. There is no funding database.
- **X.** Not collected without API access, and shown as unavailable.
- **Press.** Coverage beyond Hacker News is not collected.
- **Private hiring channels** are invisible.
- **Contracts** below federal reporting thresholds, or outside USAspending, are invisible.
- **"Since last review"** counts events detected after the last human review. With no review, it counts the window.
- **Observed deltas** need two captures. Windows dated by the events themselves are used until then.
