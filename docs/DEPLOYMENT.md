# ANTI RADAR — Deployment

## Production URL

<!-- deploy:urls:start -->
_Not deployed yet. Run `make deploy`; this block is filled in automatically._
<!-- deploy:urls:end -->

## Deploy in one command

`scripts/deploy.py` creates everything on free tiers. Re-running it is safe: it reuses what already exists and changes only what differs.

### 1. Create the tokens (about 10 minutes, once)

| `.env` key | Where to create it | Notes |
|---|---|---|
| `NEON_API_KEY` | [console.neon.tech → Account settings → API keys](https://console.neon.tech/app/settings/api-keys) | Personal key. If it sees more than one organization, also set `NEON_ORG_ID`. |
| `RENDER_API_KEY` | [dashboard.render.com → Account Settings → API Keys](https://dashboard.render.com/u/settings#api-keys) | **Sign up with GitHub**, so Render can read the repo (see [Troubleshooting](#troubleshooting)). |
| `VERCEL_TOKEN` | [vercel.com/account/settings/tokens](https://vercel.com/account/settings/tokens) | Scope: your personal account. Set `VERCEL_TEAM_ID` only if you deploy to a team. |
| `RADAR_GITHUB_TOKEN` | [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) | Fine-grained, *Public repositories (read-only)*, no extra permissions. The radar reads GitHub with it (5,000 req/h). |
| `ANTHROPIC_API_KEY` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) | Optional. Without it, text comes from deterministic templates and the deployment is strictly $0. |

The script also needs `gh` (already authenticated to `armaansingla14/Anti-Radar`) and Node.js 20+ for `npx vercel`.

### 2. Paste the tokens into the repo-root `.env`

```sh
NEON_API_KEY=...
RENDER_API_KEY=...
VERCEL_TOKEN=...
RADAR_GITHUB_TOKEN=github_pat_...
ANTHROPIC_API_KEY=sk-ant-...      # optional
```

`.env` is gitignored. The script never prints a token: every log line is redacted.

### 3. Run it

```sh
make deploy-check      # lists every missing token and where to create it (no network)
make deploy            # takes 15–25 min the first time, mostly Render Docker builds
```

Without `make`, run the same commands directly:

```sh
cd backend && uv run python ../scripts/deploy.py --check
cd backend && uv run python ../scripts/deploy.py
```

These flags are useful for previews and re-runs:

- `make deploy ARGS=--dry-run` prints the plan and every request it would send, with secrets redacted. It makes no network calls and writes no files.
- `make deploy ARGS="--step render"` runs a single step. The steps are `neon`, `render`, `vercel`, `github` and `verify`, and the flag can be repeated.

What it does, in order:

1. **neon.** Creates or reuses the project `anti-radar` in `aws-us-west-2`, then the databases `radar_live` and `radar_demo`. It then fetches a pooled URI (`DATABASE_URL`) and a direct URI (`DATABASE_URL_DIRECT`) for each database. The URIs are fetched again on every run and never written to disk.
2. **render.** Creates or reuses two free Docker web services in `oregon`, built from `backend/Dockerfile` with the repo root as the build context:
   - `radar-live`: `APP_ENV=production`, with the admin token, the GitHub token and the Anthropic key.
   - `radar-demo`: `APP_ENV=demo`, `DEMO_SNAPSHOT=true`, `SEED_ON_START=snapshot`, `LLM_PROVIDER=none`.

   Both get `TRUST_PROXY_CLIENT_IP=true`, `DB_POOL_SIZE=3` and `DB_MAX_OVERFLOW=2`. The step waits until both deploys are live. On a re-run it updates only env vars that differ, never deletes a var you added in the dashboard, and redeploys only if something changed.
3. **vercel.** Creates or reuses the project `anti-radar` (Next.js) and sets `BACKEND_URL`, `DEMO_BACKEND_URL` and `APP_ENV=production` for Production and Preview. It then runs `npx vercel@latest deploy --prod` on `frontend/` and records the production URL. Last, it sets `FRONTEND_URL` on both Render services and redeploys them only if the URL changed. On the first deploy the services boot with the provisional `https://anti-radar.vercel.app`, because startup validation needs an https `FRONTEND_URL`.
4. **github.** Sets the secrets `DATABASE_URL`, `DATABASE_URL_DIRECT` (both for the live DB), `RADAR_GITHUB_TOKEN` and `ANTHROPIC_API_KEY` (if set), passing each value on stdin rather than argv. Then sets the variable `BACKEND_URL_LIVE` and dispatches `radar-ingest` once with `job=github` for the first live capture.
5. **verify.** Checks that each of these returns 200:
   - `<live>/api/ready`
   - `<demo>/api/ready`
   - `<frontend>/api/proxy/health`
   - `<frontend>/demo`

   Then it prints a summary and writes the URLs into [Production URL](#production-url) above.

Ids and public URLs go to `deploy/state.json` (gitignored). No secrets are stored there.

**Admin token.** The first run generates a 48-character token and appends it to `.env` as `PROD_ADMIN_TOKEN`; it is deployed as `ADMIN_TOKEN` on `radar-live`. It is deliberately not stored as `ADMIN_TOKEN`: the backend reads the same `.env`, and that name would make your local development stack read-only. Paste the token at `<frontend>/unlock` to enable writes.

## Architecture and costs ($0/month)

| Piece | Where | Free-tier notes |
|---|---|---|
| Frontend | Vercel Hobby, project `anti-radar` | Non-commercial use. Server-side proxy to the backends. |
| Live backend `radar-live` | Render free web service | `APP_ENV=production`. Writes need `ADMIN_TOKEN`. |
| Demo backend `radar-demo` | Render free web service | `APP_ENV=demo`, `DEMO_SNAPSHOT=true`. Serves the verified snapshot only. |
| Databases | Neon free: one project with two databases, `radar_live` and `radar_demo` | 0.5 GB storage. pgvector is supported. |
| Scheduled ingestion | GitHub Actions cron (`radar-ingest.yml`) | Public repository, so standard runners are free. |
| Keep-warm | GitHub Actions (`keepwarm.yml`) | Pings `radar-live /api/health` during US business hours. |

The two Render services share the workspace's 750 free instance-hours per month. Keep-warm uses about 290 of them, and the demo service sleeps when idle.

The only possible variable cost is the optional Anthropic key:

- On `radar-live` it is capped at $3 per UTC day (`LLM_DAILY_USD_CAP=3`).
- Leave the key empty for a strict $0 deployment.

Two backends are needed because the data clock is pinned per process. One process cannot serve the snapshot (pinned to its as-of date) and live data at the same time.

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
| `ANTHROPIC_API_KEY` | optional | not needed (`LLM_PROVIDER=none`) |
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

Variable: `BACKEND_URL_LIVE` = the `radar-live` URL. Keep-warm uses it.

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
- `keepwarm.yml` runs every 10 minutes, 12:00–01:00 UTC, on weekdays.

## Cold starts

Render free instances sleep after 15 minutes idle, and waking one takes about 30–60 s.

- The frontend proxy waits up to 100 s for response headers. If none arrive, it returns 503 `{"detail":"backend_waking"}` and the UI shows "Waking the radar…".
- `/demo` pings `/api/proxy/health` first to wake the demo backend.
- `keepwarm.yml` keeps `radar-live` awake from 12:00 to 01:00 UTC on weekdays.
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

1. Open `<frontend>/unlock` and paste the token. After `make deploy`, it is `PROD_ADMIN_TOKEN` in `.env`.
2. The frontend checks it against `GET /api/session`.
3. If it is valid, the frontend stores it in an httpOnly, `SameSite=Strict` cookie (`radar_admin`). The proxy forwards that cookie as the header.
4. To remove the token, use the **Lock** button on `/unlock` (it calls `POST /api/admin/lock`).

The proxy also refuses cross-site writes (Origin check).

To rotate the token:

1. Change `PROD_ADMIN_TOKEN` in `.env`.
2. Run `make deploy ARGS="--step render"`. Only `ADMIN_TOKEN` changes, and `radar-live` redeploys.

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
| `radar-live /api/ready` returns 503 | The database is unreachable. Check `DATABASE_URL` on the service and that the Neon project is not suspended or over quota. |
| Frontend shows `backend_not_configured` | `BACKEND_URL`/`DEMO_BACKEND_URL` are missing or not https in Vercel. Run `make deploy ARGS="--step vercel"`. |
| Frontend shows "Waking the radar…" for a long time | A cold start. Wait about 60 s. If it persists, check the Render service's events. |
| CORS errors from the browser | `FRONTEND_URL` on Render does not match the Vercel URL. Run `make deploy ARGS="--step vercel"`, which re-syncs it. |
| Vercel: `The provided path ".../frontend/frontend" does not exist` | The project's Root Directory is set to `frontend`. Re-run the `vercel` step, which clears it. |
| Ingestion stopped | See [the 60-day caveat](#githubs-60-day-cron-caveat). Also check the `radar-ingest` run logs and the `DATABASE_URL` secret. |
| Start over | Delete `deploy/state.json`. The script finds existing resources by name. To start from nothing, delete the Neon project, both Render services and the Vercel project, then re-run. |

## Local parity

```sh
docker compose up --build              # development stack (db, api, worker, web) — same image as Render
make check-config                      # validate the current .env
APP_ENV=production ALLOW_LOCAL_PRODUCTION=true ADMIN_TOKEN=$(openssl rand -hex 24) \
  FRONTEND_URL=http://localhost:3000 docker compose up --build api
```
