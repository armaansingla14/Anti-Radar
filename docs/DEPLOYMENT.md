# ANTI RADAR — Deployment (draft)

Target cost: **$0/month**. Everything runs on free tiers:

| Piece | Where | Notes |
|---|---|---|
| Frontend | Vercel (Hobby) | project root `frontend/` |
| Live backend `radar-live` | Render free web service | `APP_ENV=production`, writes need `ADMIN_TOKEN` |
| Demo backend `radar-demo` | Render free web service | `APP_ENV=demo`, `DEMO_SNAPSHOT=true`, verified snapshot only |
| Databases | Neon free: one project, two databases `radar_live`, `radar_demo` | pgvector is supported |
| Scheduled ingestion | GitHub Actions cron (`.github/workflows/radar-ingest.yml`) | one-shot `radar sweep --only <job>` |
| Keep-warm | GitHub Actions (`keepwarm.yml`) | pings `radar-live /api/health` in US business hours |

There are two backends because the data clock is pinned per process: one process cannot serve the
snapshot (pinned to its as-of date) and live data at the same time.

## 1. Neon (databases)

1. Create a project (region close to Render's `oregon`, e.g. AWS us-west-2).
2. Create two databases: `radar_live` and `radar_demo`.
3. For each, copy two connection strings from *Connection details*:
   - **pooled**: host contains `-pooler` → `DATABASE_URL`
   - **direct**: same without `-pooler` → `DATABASE_URL_DIRECT` (Alembic migrations)

   Keep `?sslmode=require`. `postgres://` / `postgresql://` URLs are fine; RADAR rewrites them to the
   psycopg 3 driver.
4. pgvector: migration `0001` runs `CREATE EXTENSION IF NOT EXISTS vector`, which Neon allows.

Behind the pooler the app disables server-side prepared statements (`prepare_threshold=None`), recycles
connections every 300 s, and uses a small pool (`DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2` in `render.yaml`).
CLI jobs in Actions set `RADAR_NULLPOOL=1`.

Storage budget is 0.5 GB. `/status` shows the database size (`pg_database_size`). The weekly `retention`
job keeps it small.

## 2. Render (two backends)

`render.yaml` is a Blueprint. In Render choose **New → Blueprint**, select the repository, and Render creates
`radar-live` and `radar-demo` from `backend/Dockerfile`. The build context is the repo root, so `config/`
(the thesis) is baked into the image.

Render prompts for every `sync: false` variable:

| Variable | radar-live | radar-demo |
|---|---|---|
| `DATABASE_URL` | Neon pooled `radar_live` | Neon pooled `radar_demo` |
| `DATABASE_URL_DIRECT` | Neon direct `radar_live` | Neon direct `radar_demo` |
| `ADMIN_TOKEN` | **required** (`openssl rand -hex 24`) | optional (leave blank) |
| `ANTHROPIC_API_KEY` | optional (LLM text; deterministic templates otherwise) | not needed (`LLM_PROVIDER=none`) |
| `RADAR_GITHUB_TOKEN` | fine-grained read-only PAT | — |
| `FRONTEND_URL` | `https://<project>.vercel.app` | same |

The container entrypoint is `backend/start.sh`. It runs these steps in order:

1. `python -m radar.config_check`. If the configuration is invalid, it prints every problem and exits; there is no traceback.
2. `alembic upgrade head`, using `DATABASE_URL_DIRECT` when it is set.
3. `SEED_ON_START`:
   - `snapshot` (demo): loads the verified real-data snapshot, but only if the database is empty.
   - `none` (live): skips seeding.
4. `uvicorn` on `$PORT`.

Health check: `/api/health` (no database access; cheap). Readiness: `/api/ready` runs `SELECT 1` and returns 503 when the DB is unreachable.

### Production validation

With `APP_ENV=production` the API refuses to start unless all of these hold:

- `DATABASE_URL` is set and is not localhost.
- `FRONTEND_URL` is set and is https.
- `ADMIN_TOKEN` is set and is at least 16 characters.
- `THESIS_PATH` exists.

`APP_ENV=demo` applies the same rules except that `ADMIN_TOKEN` is not required. Missing optional credentials never block startup; the provider is shown on `/status` with a reason instead:

- GitHub without a token: DEGRADED, "unauthenticated — 60 req/h".
- X without a token: DISABLED, "API access not configured".
- LLM without a key: DISABLED.

To run production locally against a dockerized database, set `ALLOW_LOCAL_PRODUCTION=true`. This turns the localhost and https checks into warnings.

### Cold starts

Render free instances sleep after 15 minutes idle, and waking one takes about 30–60 s.

- The frontend proxy waits up to 100 s for response headers. On failure it returns 503 `{"detail":"backend_waking"}`, so the UI can show "Waking the radar…".
- `keepwarm.yml` keeps `radar-live` awake from 12:00 to 01:00 UTC on weekdays. That is about 290 h/month, inside the 750 free instance-hours shared by both services. The demo service sleeps when nobody is using it.

## 3. Vercel (frontend)

1. Import the repository and set **Root Directory** to `frontend` (framework is detected as Next.js).
2. Set these environment variables for Production and Preview. They are server-only; never use `NEXT_PUBLIC_`:
   - `BACKEND_URL` = `https://radar-live.onrender.com`
   - `DEMO_BACKEND_URL` = `https://radar-demo.onrender.com`
3. Deploy, then set `FRONTEND_URL` on both Render services to the Vercel URL.

In production (on Vercel, or with `APP_ENV=production`), a missing or non-https `BACKEND_URL` produces 503 `{"detail":"backend_not_configured"}`. There is never a silent fallback to `localhost`. The proxy declares `maxDuration = 300`, so SSE investigations run under Fluid compute.

**Dataset switch.** `GET /api/admin/dataset?mode=demo&next=/` sets the `radar_dataset=demo` cookie, and `mode=live` clears it. When the cookie is set, both the proxy and server-side rendering read from `DEMO_BACKEND_URL`.

## 4. Admin token (writes)

These requests are `POST`/`DELETE` calls under `/api`:

- ingest
- investigate
- memo
- feedback
- watchlist

When `ADMIN_TOKEN` is set, each of them needs the header `X-Radar-Admin: <token>`. Without it the backend answers 403 `{"detail":"read_only"}`. `GET`, `HEAD` and `OPTIONS` are always open.

To use the token from a browser:

1. Open `/unlock` and paste the token.
2. The frontend checks it against `GET /api/session`.
3. If it is valid, the frontend stores it in an httpOnly, `SameSite=Strict` cookie (`radar_admin`). The proxy forwards that cookie as the header.
4. To remove the token, use the **Lock** button on `/unlock` (it calls `POST /api/admin/lock`).

The proxy also refuses cross-site writes (Origin check).

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

The demo backend without a token accepts harmless writes (watchlist, feedback). It always refuses ingestion with 403 `snapshot_read_only`, so the verified snapshot is never contaminated.

## 5. GitHub Actions

Repository → Settings → Secrets and variables → Actions.

Secrets:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Neon pooled `radar_live` |
| `DATABASE_URL_DIRECT` | Neon direct `radar_live` |
| `RADAR_GITHUB_TOKEN` | fine-grained PAT, read-only, public repositories (5,000 req/h; Actions' own token is 1,000 req/h) |
| `ANTHROPIC_API_KEY` | optional |

Variable (or secret): `BACKEND_URL_LIVE` = `https://radar-live.onrender.com` (keep-warm).

Workflows:

- `ci.yml` runs on push and PR:
  - backend: ruff and pytest against a `pgvector/pgvector:pg16` service, including an Alembic upgrade-and-drift test;
  - frontend: `tsc`, eslint and `npm test`.
- `radar-ingest.yml` (UTC times):

  | Job | Schedule | Commands |
  |---|---|---|
  | HN | every 3 h | `hn probe score` |
  | GitHub | every 6 h | `github orgs score` |
  | arXiv | daily 04:23 | `arxiv score` |
  | Websites / careers | daily 05:53 | `web score` |
  | Morning brief | daily 11:00 | `brief` |
  | Retention | weekly, Sunday | `retention` |

  It also accepts `workflow_dispatch` with a `job` input. All runs share `concurrency: radar-ingest`, have `timeout-minutes: 30`, and run with `APP_ENV=production` and `RADAR_NULLPOOL=1`. Each run validates config (`--cli` role: no `FRONTEND_URL`/`ADMIN_TOKEN` needed), runs `alembic upgrade head` (idempotent), and then calls `uv run radar sweep --only <job>` for each job.
- `keepwarm.yml`: every 10 minutes, 12:00–01:00 UTC, on weekdays.

**60-day caveat.** GitHub disables scheduled workflows in a public repository after 60 days with no repository activity. Re-enable them from the Actions tab, or push any commit (for example a refreshed snapshot) at least every couple of months.

## 6. First deploy checklist

1. Neon: create both databases and copy 4 URLs.
2. Render: apply the Blueprint and fill in the secrets. `radar-demo` seeds the snapshot on its first boot.
3. Vercel: set the root to `frontend` and set `BACKEND_URL`/`DEMO_BACKEND_URL`.
4. Render: set `FRONTEND_URL` on both services and redeploy.
5. GitHub: add the secrets and `BACKEND_URL_LIVE`, then run `radar-ingest` manually with `job=github` to make the first live capture.
6. Check:
   - `curl https://radar-live.onrender.com/api/ready`
   - `/status` on the frontend: providers LIVE, DEGRADED or DISABLED with reasons, DB size, and data as-of.
   - `/unlock` with the admin token.

## Local parity

```sh
docker compose up --build              # development stack (db, api, worker, web) — same image as Render
make check-config                      # validate the current .env
APP_ENV=production ALLOW_LOCAL_PRODUCTION=true ADMIN_TOKEN=$(openssl rand -hex 24) \
  FRONTEND_URL=http://localhost:3000 docker compose up --build api
```
