# ANTI RADAR — Deployment

## Production URL

<!-- deploy:urls:start -->
| What | URL |
|---|---|
| Frontend | https://anti-radar-one.vercel.app |
| Guided demo | https://anti-radar-one.vercel.app/demo |
| radar-demo API | https://radar-demo-os45.onrender.com (health: `/api/health`, ready: `/api/ready`) |

Last verified by `scripts/deploy.py`: 2026-10-20T23:33:47Z.
<!-- deploy:urls:end -->

## Deploy in one command

`scripts/deploy.py` creates everything on free tiers. Re-running it is safe: it reuses what already exists and changes only what differs.

There are two shapes:

| | Demo (default) | `--live` |
|---|---|---|
| What visitors see | The whole site and `/demo` serve the **verified real-data snapshot** (collected Oct 20, 2026), labelled as such | The site serves live data refreshed by GitHub Actions; `/demo` keeps the snapshot |
| Backends | `radar-demo` | `radar-live` + `radar-demo` |
| Databases | `radar_demo` | `radar_live` + `radar_demo` |
| Scheduled ingestion | off | `radar-ingest.yml` on cron |
| Tokens | `NEON_API_KEY`, `RENDER_API_KEY`, `VERCEL_TOKEN` | the same, plus `RADAR_GITHUB_TOKEN` (and optionally `PROD_ANTHROPIC_API_KEY`) |

Start with the demo. `--live` can be added later on the same accounts without redoing anything.

### 1. Create the tokens (about 5 minutes, once)

| `.env` key | Where to create it | Notes |
|---|---|---|
| `NEON_API_KEY` | [console.neon.tech → Account settings → API keys](https://console.neon.tech/app/settings/api-keys) | Sign up with GitHub. If the key sees more than one organization, also set `NEON_ORG_ID`. |
| `RENDER_API_KEY` | [dashboard.render.com → Account Settings → API Keys](https://dashboard.render.com/u/settings#api-keys) | **Sign up with GitHub** and give Render access to the repo (see [Troubleshooting](#troubleshooting)). |
| `VERCEL_TOKEN` | [vercel.com/account/settings/tokens](https://vercel.com/account/settings/tokens) | Scope: your personal account. Set `VERCEL_TEAM_ID` only if you deploy to a team. |
| `RADAR_GITHUB_TOKEN` | [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) | `--live` only. Fine-grained, *Public repositories (read-only)*, no extra permissions (5,000 req/h). A local `GITHUB_TOKEN` is never used in its place. |
| `PROD_ANTHROPIC_API_KEY` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) | `--live` only, optional. Deliberately not `ANTHROPIC_API_KEY`, so a key exported in your shell for other work is never shipped. Without it, text comes from deterministic templates. |

The script also needs `gh` (authenticated to `armaansingla14/Anti-Radar`), Node.js 20+ for `npx vercel`, and Docker with the compose `db` service running (`docker compose up -d db`) for the seed step.

### 2. Paste the tokens into the repo-root `.env`

```sh
NEON_API_KEY=...
RENDER_API_KEY=...
VERCEL_TOKEN=...
```

`.env` is gitignored. The script never prints a token: every log line is redacted.

### 3. Run it

```sh
cd backend && uv run python ../scripts/deploy.py --check     # lists every missing token and where to create it (no network)
cd backend && uv run python ../scripts/deploy.py             # demo: ~15 min the first time, mostly the Render Docker build
cd backend && uv run python ../scripts/deploy.py --live      # later, to add the live backend + ingestion
```

With `make`: `make deploy-check`, `make deploy`, `make deploy ARGS=--live`.

Useful flags:

- `--dry-run` prints the plan and every request it would send, with secrets redacted. It makes no network calls and writes no files.
- `--step render` runs a single step. The steps are `neon`, `seed`, `render`, `vercel`, `github` and `verify`; the flag can be repeated.
- `--step seed` on its own **resets the public demo** to the committed snapshot (visitor watchlist/feedback are discarded).

What it does, in order:

1. **neon.** Creates or reuses the project `anti-radar` in `aws-us-west-2` and the database `radar_demo` (plus `radar_live` with `--live`). It fetches a pooled URI (`DATABASE_URL`) and a direct URI (`DATABASE_URL_DIRECT`) for each database. The URIs are fetched again on every run and never written to disk.
2. **seed.** Replays the committed snapshot (`backend/radar/demo/snapshot/`) into the local docker-compose database `radar_demo` with `radar seed-demo --reset --no-fictional`: offline, `LLM_PROVIDER=none`, clock pinned to the snapshot date. It then copies that database into Neon's `radar_demo` with `pg_dump | psql` (dump on stdin). The replay takes about 2 minutes on a laptop; on Render's 0.1-CPU free instance it would take far longer, so the backend's own `SEED_ON_START=snapshot` is only a fallback for an empty database.
3. **render.** Creates or reuses free Docker web services in `oregon`, built from `backend/Dockerfile` with the repo root as the build context:
   - `radar-demo`: `APP_ENV=demo`, `DEMO_SNAPSHOT=true`, `SEED_ON_START=snapshot`, `LLM_PROVIDER=none`.
   - with `--live`, `radar-live`: `APP_ENV=production`, with the admin token, the GitHub token and the optional Anthropic key.

   Each gets `TRUST_PROXY_CLIENT_IP=true`, `DB_POOL_SIZE=3` and `DB_MAX_OVERFLOW=2`. The step waits until the deploys are live. On a re-run it updates only env vars that differ, never deletes a var you added in the dashboard, and redeploys only if something changed.
4. **vercel.** Creates or reuses the project `anti-radar` (Next.js) and sets `BACKEND_URL`, `DEMO_BACKEND_URL` and `APP_ENV=production` for Production and Preview. In the demo shape both URLs point at `radar-demo`. It then runs `npx vercel@latest deploy --prod` on `frontend/`, records the production URL, and sets `FRONTEND_URL` on the Render service(s), redeploying only if the URL changed. On the first deploy the services boot with the provisional `https://anti-radar.vercel.app`, because startup validation needs an https `FRONTEND_URL`.
5. **github.** Sets the repository variable `KEEPWARM_URL` to the main backend, so `keepwarm.yml` keeps it awake in US business hours. With `--live` it also sets the secrets `DATABASE_URL`, `DATABASE_URL_DIRECT` (live DB), `RADAR_GITHUB_TOKEN` and `ANTHROPIC_API_KEY` (from `PROD_ANTHROPIC_API_KEY`, if set) on stdin, sets `RADAR_LIVE=true` (which turns on `radar-ingest`'s schedules) and dispatches `radar-ingest` once with `job=github`.
6. **verify.** Checks that `<backend>/api/ready` (each backend), `<frontend>/api/proxy/health` and `<frontend>/demo` return 200, prints a summary, and writes the URLs into [Production URL](#production-url) above.

Ids and public URLs go to `deploy/state.json` (gitignored). No secrets are stored there.

**Admin token (`--live`).** The first live run generates a 48-character token and appends it to `.env` as `PROD_ADMIN_TOKEN`; it is deployed as `ADMIN_TOKEN` on `radar-live`. It is deliberately not stored as `ADMIN_TOKEN`: the backend reads the same `.env`, and that name would make your local development stack read-only. Paste the token at `<frontend>/unlock` to enable writes. The demo backend has no token: harmless writes (watchlist, feedback) stay open and ingestion is always refused.

## Architecture and costs ($0/month)

| Piece | Where | Free-tier notes |
|---|---|---|
| Frontend | Vercel Hobby, project `anti-radar` | Non-commercial use. Server-side proxy to the backend(s). |
| Demo backend `radar-demo` | Render free web service | `APP_ENV=demo`, `DEMO_SNAPSHOT=true`. Serves the verified snapshot only. |
| Live backend `radar-live` (`--live`) | Render free web service | `APP_ENV=production`. Writes need `ADMIN_TOKEN`. |
| Database(s) | Neon free: project `anti-radar`, `radar_demo` (+ `radar_live`) | 0.5 GB storage. pgvector is supported. |
| Scheduled ingestion (`--live`) | GitHub Actions cron (`radar-ingest.yml`), gated on `RADAR_LIVE=true` | Public repository, so standard runners are free. |
| Keep-warm | GitHub Actions (`keepwarm.yml`) | Pings `KEEPWARM_URL/api/health` during US business hours. |

Render gives a workspace 750 free instance-hours per month. Keep-warm uses about 290 of them; outside those hours the service sleeps when idle.

The only possible variable cost is the optional Anthropic key on `radar-live`, capped at $3 per UTC day (`LLM_DAILY_USD_CAP=3`). Leave it empty for a strict $0 deployment.

With `--live` there are two backends because the data clock is pinned per process. One process cannot serve the snapshot (pinned to its as-of date) and live data at the same time.

## Manual setup (what the script automates)

### 1. Neon (databases)

1. Create a project in a region close to Render's `oregon`, such as AWS `us-west-2`.
2. Create two databases: `radar_live` and `radar_demo`.
3. For each database, copy two connection strings from *Connection details*:
   - **pooled**: the host contains `-pooler`. Use it as `DATABASE_URL`.
   - **direct**: the same host without `-pooler`. Use it as `DATABASE_URL_DIRECT`; Alembic migrations run through it.

   Keep `?sslmode=require`. `postgres://` and `postgresql://` URLs are both fine; RADAR rewrites them to the psycopg 3 driver.
4. pgvector: migration `0001` runs `CREATE EXTENSION IF NOT EXISTS vector`, which Neon allows.

Behind the pooler the app:

- disables server-side prepared statements (`prepare_threshold=None`);
- recycles connections every 300 s;
- uses a small pool (`DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2`).

CLI jobs in Actions set `RADAR_NULLPOOL=1`.

Storage budget is 0.5 GB. `/status` shows the database size (`pg_database_size`), and the weekly `retention` job keeps it small.

### 2. Render (two backends)

The script creates both services through the Render API. `render.yaml` is the equivalent Blueprint if you prefer **New → Blueprint** in the dashboard.

| Variable | radar-live | radar-demo |
|---|---|---|
| `DATABASE_URL` | Neon pooled `radar_live` | Neon pooled `radar_demo` |
| `DATABASE_URL_DIRECT` | Neon direct `radar_live` | Neon direct `radar_demo` |
| `ADMIN_TOKEN` | **required** (`openssl rand -hex 24`) | not set: harmless writes stay open and ingestion is always refused |
| `ANTHROPIC_API_KEY` | optional (from `PROD_ANTHROPIC_API_KEY`) | not needed (`LLM_PROVIDER=none`) |
| `GITHUB_TOKEN` / `RADAR_GITHUB_TOKEN` | fine-grained read-only PAT | — |
| `FRONTEND_URL` | `https://<project>.vercel.app` | same |
| `TRUST_PROXY_CLIENT_IP` | `true` | `true` |

The container entrypoint is `backend/start.sh`. It runs these steps in order:

1. `python -m radar.config_check`. If the configuration is invalid, it prints every problem and exits; there is no traceback.
2. `alembic upgrade head`, using `DATABASE_URL_DIRECT` when it is set.
3. `SEED_ON_START`:
   - `snapshot` (demo): loads the verified real-data snapshot, but only if the database is empty.
   - `none` (live): skips seeding.
4. `uvicorn` on `$PORT`.

There are two probes:

- `/api/health` is the health check. It does no database access, so it is cheap.
- `/api/ready` is the readiness check. It runs `SELECT 1` and returns 503 when the database is unreachable.

**Production validation.** With `APP_ENV=production` the API refuses to start unless all of these hold:

- `DATABASE_URL` is set and is not localhost.
- `FRONTEND_URL` is set and is https.
- `ADMIN_TOKEN` is set and is at least 16 characters.
- `THESIS_PATH` exists.

`APP_ENV=demo` applies the same rules except that `ADMIN_TOKEN` is not required.

Missing optional credentials never block startup. Instead, the affected provider is shown on `/status` with a reason:

- GitHub without a token: DEGRADED, "unauthenticated — 60 req/h".
- X without a token: DISABLED, "API access not configured".
- LLM without a key: DISABLED.

### 3. Vercel (frontend)

The script deploys `frontend/` with the Vercel CLI, so the project's **Root Directory** stays empty.

If you instead import the repository through Vercel's Git integration:

1. Set Root Directory to `frontend`.
2. Do not run the script's `vercel` step against that project. It resets Root Directory to empty.

Set these environment variables for Production and Preview. They are server-only; never use `NEXT_PUBLIC_`:

- `BACKEND_URL` = the `radar-live` URL
- `DEMO_BACKEND_URL` = the `radar-demo` URL
- `APP_ENV` = `production`

In production (on Vercel, or with `APP_ENV=production`), a missing or non-https `BACKEND_URL` produces 503 `{"detail":"backend_not_configured"}`. There is never a silent fallback to `localhost`. The proxy declares `maxDuration = 300`, so SSE investigations run under Fluid compute.

**Dataset switch.** `GET /api/admin/dataset?mode=demo&next=/` sets the `radar_dataset=demo` cookie, and `mode=live` clears it. While the cookie is set, both the proxy and server-side rendering read from `DEMO_BACKEND_URL`.

### 4. GitHub Actions

Go to Repository → Settings → Secrets and variables → Actions.

Secrets:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Neon pooled `radar_live` |
| `DATABASE_URL_DIRECT` | Neon direct `radar_live` |
| `RADAR_GITHUB_TOKEN` | fine-grained PAT, read-only, public repositories (5,000 req/h; Actions' own token gets 1,000 req/h) |
| `ANTHROPIC_API_KEY` | optional |

Variables:

- `KEEPWARM_URL` = the main backend URL (`radar-demo` for a demo deploy, `radar-live` with `--live`). Keep-warm uses it.
- `RADAR_LIVE` = `true` only when the live backend exists. Without it, `radar-ingest`'s schedules are skipped (a manual dispatch still runs).

Workflows:

- `ci.yml` runs on every push and pull request:
  - backend: ruff, and pytest against a `pgvector/pgvector:pg16` service, including an Alembic upgrade-and-drift test;
  - frontend: `tsc`, eslint and `npm test`.
- `radar-ingest.yml` runs these jobs (times in UTC):

  | Job | Schedule | Commands |
  |---|---|---|
  | HN | every 3 h | `hn probe score` |
  | GitHub | every 6 h | `github orgs score` |
  | arXiv | daily 04:23 | `arxiv score` |
  | Websites / careers | daily 05:53 | `web score` |
  | Morning brief | daily 11:00 | `brief` |
  | Retention | weekly, Sunday | `retention` |

  It also accepts `workflow_dispatch` with a `job` input. All runs share `concurrency: radar-ingest`, have `timeout-minutes: 30`, and run with `APP_ENV=production` and `RADAR_NULLPOOL=1`. Each run:

  1. validates config with the `--cli` role, which needs no `FRONTEND_URL` or `ADMIN_TOKEN`;
  2. runs `alembic upgrade head`, which is idempotent;
  3. calls `uv run radar sweep --only <job>` for each job.
- `keepwarm.yml` runs every 10 minutes, 12:00–01:00 UTC, on weekdays, and does nothing until `KEEPWARM_URL` is set.

## Cold starts

Render free instances sleep after 15 minutes idle, and waking one takes about 30–60 s.

- The frontend proxy waits up to 100 s for response headers. If none arrive, it returns 503 `{"detail":"backend_waking"}` and the UI shows "Waking the radar…".
- `/demo` pings `/api/proxy/health` first to wake the demo backend.
- `keepwarm.yml` keeps the main backend awake from 12:00 to 01:00 UTC on weekdays.
- The first request after a quiet night will be slow. That is expected on the free tier.

## Admin token (writes)

These requests are `POST`/`DELETE` calls under `/api`:

- ingest
- investigate
- memo
- feedback
- watchlist

When `ADMIN_TOKEN` is set, each of them needs the header `X-Radar-Admin: <token>`. Without the header the backend answers 403 `{"detail":"read_only"}`. `GET`, `HEAD` and `OPTIONS` are always open.

To use the token from a browser:

1. Open `<frontend>/unlock` and paste the token. After `deploy.py --live`, it is `PROD_ADMIN_TOKEN` in `.env`.
2. The frontend checks it against `GET /api/session`.
3. If it is valid, the frontend stores it in an httpOnly, `SameSite=Strict` cookie (`radar_admin`). The proxy forwards that cookie as the header.
4. To remove the token, use the **Lock** button on `/unlock` (it calls `POST /api/admin/lock`).

The proxy also refuses cross-site writes (Origin check).

To rotate the token:

1. Change `PROD_ADMIN_TOKEN` in `.env`.
2. Run `deploy.py --live --step render`. Only `ADMIN_TOKEN` changes, and `radar-live` redeploys.

`GET /api/session` returns:

```json
{
  "app_env": "...",
  "read_only": true,
  "admin": false,
  "demo_snapshot": false,
  "demo_synthetic": false,
  "snapshot_as_of": null
}
```

The UI uses it to hide write controls when `read_only` is true.

The demo backend has no token. It accepts harmless writes (watchlist, feedback) but always refuses ingestion with 403 `snapshot_read_only`, so the verified snapshot is never contaminated.

## GitHub's 60-day cron caveat

GitHub disables scheduled workflows in a public repository after 60 days without repository activity. When that happens, ingestion stops and the live radar goes stale; the demo is unaffected. To prevent it, do either of these at least every couple of months:

- push any commit, for example a refreshed snapshot;
- re-enable `radar-ingest` and `keepwarm` from the Actions tab.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make: command not found` | Run `cd backend && uv run python ../scripts/deploy.py` directly, or install make (`sudo apt install make`). |
| `Render cannot read https://github.com/armaansingla14/Anti-Radar` | Render's GitHub app has no access to the repo. Open [dashboard.render.com/web/new](https://dashboard.render.com/web/new) → **GitHub** → **Connect GitHub** (or *Configure account*), then grant the Render app access to `armaansingla14/Anti-Radar` at [github.com/apps/render/installations/new](https://github.com/apps/render/installations/new). Re-run `make deploy`. |
| `Your Neon key sees several organizations` | Add `NEON_ORG_ID=<id>` (printed in the error) to `.env`. |
| Render deploy `build_failed` / `update_failed` | Open the logs link the script prints. A config problem appears as `ANTI RADAR configuration invalid…` at the top of the deploy log, listing each problem. |
| `/api/ready` returns 503 | The database is unreachable. Check `DATABASE_URL` on the service and that the Neon project is not suspended or over quota. |
| Frontend shows `backend_not_configured` | `BACKEND_URL`/`DEMO_BACKEND_URL` are missing or not https in Vercel. Run `make deploy ARGS="--step vercel"`. |
| Frontend shows "Waking the radar…" for a long time | A cold start. Wait about 60 s. If it persists, check the Render service's events. |
| CORS errors from the browser | `FRONTEND_URL` on Render does not match the Vercel URL. Run `make deploy ARGS="--step vercel"`, which re-syncs it. |
| Vercel: `The provided path ".../frontend/frontend" does not exist` | The project's Root Directory is set to `frontend`. Re-run the `vercel` step, which clears it. |
| Seed step: `docker compose exec` fails | Start the local database first: `docker compose up -d db`. |
| Ingestion never runs | Expected for a demo deploy. With `--live`, check that the repository variable `RADAR_LIVE` is `true`. |
| Ingestion stopped | See [the 60-day caveat](#githubs-60-day-cron-caveat). Also check the `radar-ingest` run logs and the `DATABASE_URL` secret. |
| Start over | Delete `deploy/state.json`. The script finds existing resources by name. To start from nothing, delete the Neon project, the Render service(s) and the Vercel project, then re-run. |

## Local parity

```sh
docker compose up --build              # development stack (db, api, worker, web) — same image as Render
make check-config                      # validate the current .env
APP_ENV=production ALLOW_LOCAL_PRODUCTION=true ADMIN_TOKEN=$(openssl rand -hex 24) \
  FRONTEND_URL=http://localhost:3000 docker compose up --build api
```
