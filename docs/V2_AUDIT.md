# ANTI RADAR — V2 Audit

Audited 2026-10-20 against `main` @ 4d80a08. The audit was read-only and was done in four passes (lenses): backend, frontend, data, and deploy. Findings that came up in more than one pass are listed once.

## Summary

The core engine is sound, and in places better than a prototype needs to be. It has:

- conservative entity resolution
- evidence-carrying scores
- provenance on every fact
- a reproducible real-data snapshot (Oct 6, 2026) replayed through the normal ingest pipeline
- an app clock that pins "today" honestly

Three things are wrong with the product as it ships today:

1. **The default experience leads with fictional data.** Fictional companies hold radar ranks #1, #2, #3 and #5, the top 4 founders are fictional, and the `/thesis` headline is driven by fictional data.
2. **It cannot be deployed publicly as-is.** There is no auth on writes, no deploy config, the worker is long-running, and it seeds fictional data on startup.
3. **The UI puts the score first and WHY NOW second.** It also hides how old the data is ("80 signals · 24h" on a 14-day-old snapshot).

V2 work is mostly re-plumbing and re-presentation of data that already exists. It is not new scoring.

## What already works

- **Ingestion observability.** Every provider run writes an `IngestionRun` row with an ok/partial/error status (`backend/radar/pipeline/runner.py`).
- **Entity resolution** (`backend/radar/pipeline/resolve.py:1-23`):
  - exact keys are matched first;
  - fuzzy name matches are only proposed for review (`MatchCandidate`) and never auto-merged;
  - arXiv authors are linked to GitHub users only when there is evidence.
- **The `is_demo` flag** is on every table.
  - Peer groups for scoring and strange-signal detection exclude demo rows (`scoring/anti_score.py:71`, `detectors/strange.py:479`).
  - Feeds cap demo rows at half of each list (`services/feed.py:224`).
  - Fictional records use `.example` domains and `demo-` GitHub orgs (`demo/fictional.yaml`).
- **Scoring explains itself.**
  - Collision saturates within a source family, adds a bonus for independent families, and skips repeated metric readings (`scoring/collision.py:97`).
  - Attention Gap gives no credit when attention was never measured (`scoring/attention.py:62,102`).
  - The founder score has 6 components, including `formation` (`scoring/founder.py:128-212`).
  - Each component carries the ids of its evidence signals.
- **Provenance**:
  - Epistemic class on each fact (FACT/INFERENCE/MODEL_OPINION);
  - `FieldProvenance` with source URL and last-updated time;
  - `Source` rows with `fetched_at`, `content_hash` and `http_status`;
  - numbered source references S1..Sn (`services/entities.py:428-441`);
  - the agent's `SourceRegistry` plus citation validation (`agent/tools.py:102`).
- **Observed vs reconstructed history.** `MetricSnapshot.source` is one of observed, backfill or demo: 806 rows are observed and 7,259 are backfill. The UI labels backfilled points as reconstructed (`services/entities.py:508-514`).
- **Snapshot replay.**
  - `backend/radar/demo/snapshot/` holds 32 gzipped provider runs plus `MANIFEST.json`.
  - The snapshot contains 253 companies, 1,116 people, 194 repos, 145 papers and 814 signals.
  - `clock.py` resolves "today" in this order: the override, then the snapshot pin, then wall time. `wall_now()` stays unpinned for operational timestamps.
  - `backfill_first_seen` dates first-seen from evidence, not load time.
- **X providers.** Official, manual, disabled and mock providers all exist (`backend/radar/providers/x/`).
- **LLM guards.** There is a cost guard, a daily cap of $10, a response cache and citation checks (`llm/guard.py`, `llm/cost.py`).
- **Frontend.**
  - All 12 routes return 200.
  - The editorial design system is consistent across them.
  - Detail pages have INFERRED/UNKNOWN badges, FACT chips, [S1..] citations and a founder graph.
  - Keyboard support: J/K/O/Y/W/I and a Cmd-K palette.
  - `/retrospective` is honest ("not a backtest").
- **Deploy-ready parts.**
  - The proxy (`frontend/app/api/proxy/[...path]/route.ts`) has an allow-list, a 256KB body cap, no-redirect, and SSE passthrough.
  - Settings come only from env vars.
  - Alembic uses NullPool.
  - `/api/health` does not touch the DB.
  - Migration 0001 uses pgvector, which Neon supports.

## What is incomplete

- **Fictional by default.**
  - `demo_mode: bool = True` (`backend/radar/settings.py:43-44`).
  - Every route falls back to it (`api/routes/radar.py:23`, `companies.py:37`, `founders.py:37`, `signals.py:33`, `brief.py`).
  - On the frontend the demo filter is opt-out (`app/companies/page.tsx:31`, `app/founders/page.tsx:29`).
- **Official X is never called.**
  - `get_x_provider` is used only in tests.
  - Only the manual path is wired in (`ops.py:150`, `api/routes/ingest.py:155`).
  - Person X attention is hardcoded as unavailable (`scoring/founder.py:166`).
- **`ops.probe_attention` (`ops.py:196`) is never scheduled**, but Attention Gap and quiet-velocity depend on it (`scoring/extract.py:454`).
- **The morning brief is never saved.** It is recomputed on every request (`services/brief.py:328`). `ResearchReport.kind='morning_brief'` exists (`models/core.py:507`) but nothing writes it, so there is no `/brief/YYYY-MM-DD`.
- **No relationship or team tables.** The graph is rebuilt on every request (`services/graph.py:91-199`). Formation detection is phrase matching only (`providers/formation.py`), even though 733 author and 398 contributor relations already exist.
- **Data age is per provider only** (`services/feed.py:424`). There is no per-entity `last_observed_at`.
- **No source drawer.** The `Source` rows needed to build one are already stored.
- **Frontend: not built yet.** Formation Radar, Team Convergence, Meet, meeting briefs, brief history, `/demo`, source drawers, per-entity data age, and the X provider mode.
- **Frontend bugs.**
  - Some descriptions render as solid black blocks. These are CJK or emoji characters with no font fallback (TokenRhythm, RedNote).
  - `/status` reports "3 of 6" in the headline but the tile and footer report 3/7, because the footer counts pseudo-providers (`components/Footer.tsx:20`).
  - The footer's 2.5s race (`Footer.tsx:10`) shows "backend not reachable" under load.
  - `/status` has a hydration-mismatch warning on the AddToRadar inputs.

## What is redundant

- **Two copies of the full sweep:** `ops.sweep` (`ops.py:299`) and `cli ingest all` (`cli.py:133`). Neither runs `probe_attention`.
- **Three feed builders** re-run the same queries: `radar_feed`, `daily_radar` and `morning_brief` (`services/feed.py:441,603`, `services/brief.py:328`). In the UI, `/`, `/daily` and `/brief` show nearly the same lists.
- **Confusing duplicate names:**
  - `radar/retro.py` vs `services/retro.py`;
  - `services/memo.py` (view cache) vs `agent/memo.py` (the Anti Memo);
  - the alias `SignalSnapshot = MetricSnapshot` (`models/core.py:349`).
- **Pseudo-provider "snapshot" and "demo" runs** appear in `data_freshness`. The demo run shows the seed time, which confuses which date the data is as of.
- **Dev-only once cron moves to Actions:** the `worker` service and APScheduler. `frontend/Dockerfile` is not used on Vercel.
- **`score_history`** (`demo/seed.py:240`): backfilled score history. Retire it once real daily history exists.

## What should be preserved

- The `Component`, `CollisionResult` and `AttentionResult` dataclasses, and the per-day `ScoreSnapshot.components/attention/collision`. WHY NOW and Collision 2.0 should be built from these fields.
- The rules in `resolve.py` and the `MatchCandidate` review queue.
- `IngestionRun` and the `run_provider` wrapper.
- The `clock.py` precedence, `wall_now` vs `now`, and `is_live_run` releasing the pin.
- The snapshot file format (provider/params/run/batch) and its replay through `ingest_batch`.
- The strange-signal detectors `shared_prior_employer_new_org`, `lab_cluster` and `affiliation_exodus` (`detectors/strange.py:193,260,409`). They are proto Formation Radar and Team Convergence.
- The founder `formation` component and the phrase matcher in `providers/formation.py`.
- The `is_demo` columns, the `.example` convention, the `MetricSnapshot.source` distinction, `Signal.epistemic`, and `FieldProvenance`.
- The proxy allow-list, body cap, header filtering and SSE passthrough (`route.ts:14-99`). `BACKEND_URL` stays server-only.
- The visual language (`Masthead.tsx`, `globals.css`), the keyboard model, and the INFERRED/UNKNOWN and "model opinion" chips.

## Current test status

| Suite | Command | Result |
|---|---|---|
| Backend | `cd backend && TEST_DATABASE_URL=postgresql+psycopg://radar:radar@localhost:5432/radar_test_audit uv run pytest -q -p no:cacheprovider` | **528 passed**, 0 failed (227.5s) |
| Frontend | `cd frontend && npm test` (node:test) | **24 pass, 0 fail** (10 suites) |

Gaps in the tests:

- The tests build the schema with `Base.metadata.create_all` (`backend/tests/conftest.py:56`), not Alembic, so drift between the models and the migrations goes undetected.
- No test asserts that the default `/api/radar` contains no `is_demo` rows.
- There is no CI (`.github/workflows/` does not exist).

## Deployment blockers

1. **No auth on writes.**
   - Nine POST/DELETE routes are reachable through the public proxy, because `ingest`, `investigate`, `memo`, `feedback` and `watchlist` are all in `ALLOWED_SECTIONS` (`route.ts:14-33`). Those routes are ingest/github, ingest/url, ingest/x, investigate, memo, feedback, watchlist and others.
   - Anyone could spend GitHub quota and LLM budget and write to the DB.
   - "Dismiss" ("will not resurface", `components/feed/RadarCards.tsx:41`) would act globally for every visitor.
   - There is only a single default user (`services/feed.py:98`).
2. **Fictional data on startup and by default.**
   - `docker-compose.yml:39` runs `seed-demo --if-empty`.
   - `seed_demo` defaults to `fictional=True` (`demo/seed.py:255`).
   - `.env.example` sets `DEMO_MODE=true`.
3. **No deploy config.** There is no `render.yaml`, no `.github/workflows/`, and no `vercel.json`. The proxy route has no `maxDuration`, so SSE investigations can be cut off.
4. **The worker is a forever-running `BlockingScheduler`** (`worker.py`). It is incompatible with the Render free tier, so the cron must move to GitHub Actions calling one-shot CLI commands.
5. **Backend Dockerfile.** It hardcodes `--port 8000` instead of `$PORT` and runs no migrations.
   - Settings has a single `database_url`.
   - Neon needs a direct URL for migrations and a pooled URL at runtime.
6. **The thesis config is outside the build context.** `settings.py:19` resolves to `ROOT/config/anti_fund_thesis.yaml`, and compose only provides it through a volume mount.
7. **Silent localhost fallbacks.** These turn missing env vars into opaque 502s:
   - `BACKEND_URL ?? 'http://localhost:8000'` (`route.ts:40`, `lib/api.ts:66`);
   - the default `database_url` (`settings.py:18`).
8. **The clock pin is read only at lifespan start.** A running Render instance keeps the snapshot date until it restarts, even after the cron has written live data.
9. **Bug: `refine_topics_with_llm`** (`ops.py:282-283`) inserts a duplicate `EntityTopic` row, which violates `uq_entity_topic`. As a result, `recompute(use_llm_topics=True)` fails at commit.

Deployment issues that are not blockers:

- The DB pool (`db.py:20`, 10+10 connections) is too large for Neon free. It needs `pool_recycle`, and `prepare_threshold=None` when going through PgBouncer.
- `lib/api.ts` fetches with no timeout, and every fetch uses `cache: 'no-store'`. A 30-60s cold start on Render would hang SSR.
- `resolved_github_token` shells out to `gh` (`settings.py:51`).
- The in-process rate limiter and view cache reset on every spin-down.
- No list of the required production env vars exists.

## Data blockers

- **Fictional data outranks real data, and will keep doing so.** Fictional dates are relative to `clock.today()` (`demo/fictional.py:53-60`), so the fictional set always looks fresh while the real data ages.
- **History is shallow.** There are 806 observed metric rows, all from one day. Velocity and attention depend on backfill.
  - Star backfill needs `GITHUB_TOKEN`. Without a token, `github.py:758` fetches 1 page.
  - The `gh` CLI fallback will not exist on Render or Actions.
- **The snapshot is partial.**
  - arXiv timed out on cs.AR/cs.OS (7 errors).
  - GitHub is partial (one `commaai` stats request returned 202).
  - 123 of 194 repos are HN-discovered stubs (0 stars, no `gh_created_at`).
- **The snapshot contains no real X data or ATS/job data.** Every X-family and hiring signal in the default view is fictional.
- **All 814 snapshot signals are `epistemic=FACT`,** including derived ones such as `star_velocity` and `commit_acceleration`.

## Product weaknesses (prototype vs investment tool)

- **The score dominates.** Each card has a crimson numeral of about 64px, while WHY NOW is a small grey list. The home page is 7,741px tall: 12 near-identical cards with no summary table.
- **Staleness is hidden.** The masthead says 20 Oct, while the home page shows "Tuesday, October 6" and "80 new signals overnight" (`app/page.tsx:32-34,123`).
- **The LLM text undermines trust.**
  - `/brief` leaks internal ids ("I2 says six people but names only four").
  - The counts in item 02 do not match the list.
  - The `/daily` summary admits it has no detail.
  - The precomputed narratives mix real and fictional entities.
- **Entity names read like a scrape.**
  - Companies named after repos or taglines: "V-Modal AI - Visual Memory Layer: Find, recall…", `awesome-dsh-plugin`.
  - Founders shown by GitHub handle (`fryzcjy`).
  - vLLM contributors with identical evidence.
  - Free-text `current_company` values ("✖️", BibTeX escapes).
- **"Formation" relies on GitHub org age alone.** That mixes academic labs (RUC-NLPIR), big companies (Baidu, Hugging Face) and awesome-lists in with real new companies.
- **Person attention-gap inputs have only 2 real sources** (GitHub followers and HN mentions), so coverage is usually "low".
- **Page weight.** `/thesis` is 713KB of HTML and `/` is 350KB.
- **Jargon.** `/status` shows "Score 2026.10-1" and lists the demo dataset as a provider.

## Planned V2 changes

| Area | Change |
|---|---|
| **Deployment** | <ul><li>`render.yaml`: free web service; `uvicorn … --port $PORT`; preDeploy `alembic upgrade head` on `DATABASE_URL_DIRECT`; health check `/api/health`.</li><li>`.github/workflows/ingest.yml`: sweep every 6h plus a daily brief, using secrets `GITHUB_TOKEN`, `DATABASE_URL_DIRECT` and `ANTHROPIC_API_KEY`.</li><li>`.github/workflows/ci.yml`: pytest with a pgvector service, plus `npm test`.</li><li>Settings: `env`, `database_url_direct`, `admin_token`, and pool sizes from env. Fail loudly on missing `DATABASE_URL`/`BACKEND_URL`/`THESIS_PATH` in prod.</li><li>`db.py`: smaller pool, `pool_recycle`, `prepare_threshold=None` on `-pooler` hosts.</li><li>Writes gated by `ADMIN_TOKEN` in FastAPI and the proxy, or `READ_ONLY=1`, with the write controls hidden in the UI.</li><li>`maxDuration=300` on the proxy; `AbortSignal.timeout` and a "waking backend…" state in `lib/api.ts` and `Footer.tsx`.</li><li>Refresh the clock pin on a short TTL instead of only at lifespan start.</li></ul> |
| **Real data / verified snapshot** | <ul><li>Rename `demo_mode` to `synthetic_enabled` (default False), with one shared dependency in `api/deps.py`.</li><li>Fictional data only with `?synthetic=1` or `SYNTHETIC_DATA=1`, under a persistent SYNTHETIC banner.</li><li>`radar seed-snapshot` loads real data only; prod never seeds at startup.</li><li>Pin fictional dates to a fixed date.</li><li>Test: default `/api/radar` has zero `is_demo` rows.</li><li>Optional weekly commit of a refreshed snapshot from Actions.</li><li>Drop the pseudo-providers from freshness.</li></ul> |
| **GitHub engine** | <ul><li>Live cron with a repo-secret token, so star and commit backfill are complete.</li><li>Fill in the stub repos.</li><li>Filter by kind: exclude `awesome-*`, list or interview repos, and known big companies and universities.</li><li>Normalize display names (`owner/repo` becomes a project name; strip taglines).</li><li>Downgrade velocity and acceleration signals from FACT to inference.</li></ul> |
| **Formation Radar + Team Convergence** | <ul><li>New `services/formation.py` that combines the `formation` component, the formation-language signals and the 3 strange detectors.</li><li>New tables `formation_clusters` (members, evidence ids, first_seen, last_seen) and `person_relationships` (kind: co-contributor, co-author, shared-employer or co-founder; strength; evidence), filled in `score_all`.</li><li>`services/graph.py` reads from `person_relationships` instead of rebuilding.</li><li>Formation signals: orgs younger than 180 days, `paper_with_code` pointing to a new org, and normalized self-reported affiliation labeled as such.</li><li>Routes `/formation` and team convergence on the founder graph.</li></ul> |
| **Who Should We Meet + Meeting Brief** | <ul><li>New `services/meet.py`: rank on the latest `ScoreSnapshot` by score change, collision families, gap flag and formation, excluding dismissed or known entities.</li><li>Briefs reuse `agent/memo.py` and `agent/tools.py`, with a deterministic template when the LLM is off.</li><li>Route `/meet`.</li></ul> |
| **WHY NOW hierarchy** | <ul><li>Build WHY NOW deterministically in `presenters.py` from `collision.summary`, `family_strength`, `span_days`, the attention result and `component.evidence`.</li><li>On cards: one sentence plus 2-3 dated, sourced bullets in body type, with the score as a small secondary badge.</li></ul> |
| **Collision 2.0** | Expose per-family evidence and timing (span, order of arrival) from the existing `CollisionResult`, and show it in WHY NOW and the source drawer. |
| **Attention Gap 2.0** | <ul><li>Schedule `probe_attention` in the sweep.</li><li>Feed X followers in when Official X is active.</li><li>Show coverage explicitly, and never imply a gap when attention is unmeasured.</li></ul> |
| **Data age + source drawer** | <ul><li>Per-entity `last_observed_at` computed in recompute (max of `Signal.event_at`, `MetricSnapshot.day` and `FieldProvenance.last_updated`).</li><li>Chip such as "last observed 13d ago", with an observed vs backfill badge.</li><li>Drawer built on the `SourceRef` list (`entities.py:428`) with snippet and `fetched_at`.</li><li>Replace the "24h/overnight" copy with "Snapshot of <date> · N days old".</li></ul> |
| **Home redesign** | <ul><li>Merge `/` and `/daily` into one view.</li><li>WHY NOW-led cards and a compact table view.</li><li>Fix the font fallback for CJK and emoji.</li><li>ISR/revalidate for snapshot data; paginate `/thesis`.</li></ul> |
| **Unknown Yesterday** | First-seen on the app clock is already correct (4d80a08). Surface entities whose `first_seen` is within N days and that have at least 2 source families, without relying on the score. |
| **Strange Signals** | <ul><li>Keep the detectors and exclude demo rows by default.</li><li>Route the people-centric ones into Formation and Convergence.</li><li>Fix free-text affiliation noise.</li></ul> |
| **Daily Radar + brief history** | <ul><li>Fold `cli ingest all` into `ops.sweep` (adding probe_attention, recompute and brief persistence) as one `radar sweep`.</li><li>Persist the brief (`ResearchReport kind='morning_brief'` or a `daily_briefs` table).</li><li>`GET /api/brief/{date}` plus a list route; dynamic `/brief/[date]` with a history index.</li><li>Collapse the three feed builders.</li></ul> |
| **Feedback v2** | <ul><li>Per-user or session identity in place of the single default user.</li><li>Feedback writes behind the admin token.</li><li>Dismiss scoped to the user.</li><li>Feed dismissals and known entities into the Meet ranking.</li></ul> |
| **X providers** | <ul><li>Disabled by default, Manual as the honest path, and Official only when `X_BEARER_TOKEN` is set.</li><li>Wire `get_x_provider` into ops.</li><li>Report the mode in `services/status.py` and the UI.</li><li>Hide X lanes when disabled.</li></ul> |
| **/demo + video** | <ul><li>`/demo` guided tour, the only place the synthetic set appears.</li><li>A verified-snapshot walkthrough on real data.</li><li>Record the video from the deployed app.</li></ul> |
| **README / docs** | <ul><li>Deploy section listing every prod env var (pooled vs direct Neon URLs, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `X_BEARER_TOKEN`, `ADMIN_TOKEN`, `CORS_ORIGINS`, `SYNTHETIC_DATA`).</li><li>Data trust model: real vs synthetic, observed vs backfill.</li><li>Test that runs `alembic upgrade head` and checks the models and migrations match.</li></ul> |

Fixes to make immediately: the duplicate `EntityTopic` insert (`ops.py:282`), the provider count mismatch, the hydration warning, LLM narratives that leak internal ids (validate the text and reject `I\d+` ids and count mismatches), and display-name normalization.
