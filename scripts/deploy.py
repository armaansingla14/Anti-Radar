#!/usr/bin/env python3
"""ANTI RADAR: one-command $0 deploy (Neon + Render + Vercel [+ GitHub Actions]).

    cd backend && uv run python ../scripts/deploy.py --check      # are the tokens in .env? (no network)
    cd backend && uv run python ../scripts/deploy.py --dry-run    # print every request; no network, no writes
    cd backend && uv run python ../scripts/deploy.py              # deploy the demo (idempotent; safe to re-run)
    cd backend && uv run python ../scripts/deploy.py --live       # ... plus the live backend + scheduled ingestion
    cd backend && uv run python ../scripts/deploy.py --step render

Default ("demo"): ONE backend serving the verified real-data snapshot; the whole site and /demo use it.
Needs only NEON_API_KEY, RENDER_API_KEY and VERCEL_TOKEN.
--live adds radar-live (its own database, fed by GitHub Actions cron) as the main site's backend; /demo keeps
the snapshot. Also needs RADAR_GITHUB_TOKEN (and optionally PROD_ANTHROPIC_API_KEY).

Steps (in order): neon -> seed -> render -> vercel -> github -> verify.

* neon    project `anti-radar` (aws-us-west-2), database radar_demo (+ radar_live), pooled + direct URIs.
* seed    rebuild the snapshot locally (`radar seed-demo` into the docker-compose Postgres), then restore that
          database into Neon's radar_demo (pg_dump | psql). Re-running resets the public demo to the snapshot.
* render  free web service radar-demo (+ radar-live) from backend/Dockerfile (repo-root context); waits for deploys.
* vercel  project `anti-radar`, env BACKEND_URL / DEMO_BACKEND_URL / APP_ENV, `vercel deploy --prod` of frontend/,
          then FRONTEND_URL on the Render services (redeploy only if it changed).
* github  KEEPWARM_URL variable (keepwarm.yml pings the main backend in US business hours); with --live also
          Actions secrets, RADAR_LIVE=true (enables radar-ingest's schedules) and one `radar-ingest` run.
* verify  /api/ready on the backends, /api/proxy/health and /demo on the frontend; writes the URLs into
          docs/DEPLOYMENT.md and deploy/state.json.

Tokens are read from the repo-root .env and never printed: every log line goes through `Ctx.redact`.
deploy/state.json (gitignored) keeps only ids and public URLs; database URLs are re-fetched from Neon each run.
The production admin token is kept in .env as PROD_ADMIN_TOKEN, not ADMIN_TOKEN, because the backend also
reads that .env and an ADMIN_TOKEN there would make local development read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]

GITHUB_REPO = "armaansingla14/Anti-Radar"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
BRANCH = "main"

NEON_API = "https://console.neon.tech/api/v2"
NEON_PROJECT = "anti-radar"
NEON_REGION = "aws-us-west-2"  # Oregon, same metro as Render's `oregon`
NEON_PG_VERSION = 17
DATABASES = {"live": "radar_live", "demo": "radar_demo"}

RENDER_API = "https://api.render.com/v1"
RENDER_REGION = "oregon"
SERVICES = {"live": "radar-live", "demo": "radar-demo"}
# The public demo backend stays open for harmless writes (watchlist, feedback); ingestion is always refused there.
LOCK_DEMO_WRITES = False

VERCEL_API = "https://api.vercel.com"
VERCEL_PROJECT = "anti-radar"
# Render needs an https FRONTEND_URL to boot (config_check). Until Vercel assigns the real one, use the
# predictable default; the vercel step replaces it (and redeploys only if it differs).
PROVISIONAL_FRONTEND_URL = f"https://{VERCEL_PROJECT}.vercel.app"

STEPS = ("neon", "seed", "render", "vercel", "github", "verify")
DEPLOY_DONE = {"live"}
DEPLOY_FAILED = {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}
RENDER_FIX = f"""Render cannot read {REPO_URL}.
One-time fix (about 30 seconds):
  1. Open https://dashboard.render.com/web/new and choose "GitHub" -> "Connect GitHub"
     (or "Configure account" if GitHub is already linked).
  2. On GitHub, give the Render app access to {GITHUB_REPO} ("All repositories" or select it):
     https://github.com/apps/render/installations/new
  3. Re-run: make deploy   (or: cd backend && uv run python ../scripts/deploy.py --step render)"""


@dataclass(frozen=True)
class Token:
    name: str
    required: bool
    where: str
    how: str
    live_only: bool = False


TOKENS = (
    Token("NEON_API_KEY", True, "https://console.neon.tech/app/settings/api-keys",
          "Neon -> Account settings -> API keys -> Create new API key."),
    Token("RENDER_API_KEY", True, "https://dashboard.render.com/u/settings#api-keys",
          "Render (sign up with GitHub) -> Account Settings -> API Keys -> Create API Key."),
    Token("VERCEL_TOKEN", True, "https://vercel.com/account/settings/tokens",
          "Vercel -> Account Settings -> Tokens -> Create (scope: your personal account)."),
    Token("RADAR_GITHUB_TOKEN", True, "https://github.com/settings/personal-access-tokens/new",
          "Fine-grained token, Repository access: 'Public repositories', no extra permissions "
          "(the radar reads GitHub at 5,000 req/h). Only for --live.", live_only=True),
    # Deliberately not ANTHROPIC_API_KEY: that name is often set in the shell for other work, and only a key meant for this deployment may be shipped.
    Token("PROD_ANTHROPIC_API_KEY", False, "https://console.anthropic.com/settings/keys",
          "Optional, --live only: LLM text on radar-live; without it RADAR uses deterministic templates.",
          live_only=True),
)


def step_tokens(step: str, live: bool) -> tuple[str, ...]:
    return {
        "neon": ("NEON_API_KEY",),
        "seed": ("NEON_API_KEY",),
        "render": ("NEON_API_KEY", "RENDER_API_KEY"),
        "vercel": ("VERCEL_TOKEN", "RENDER_API_KEY"),
        "github": ("NEON_API_KEY", "RADAR_GITHUB_TOKEN") if live else (),
        "verify": (),
    }[step]


class DeployError(RuntimeError):
    pass


class RepoAccessError(DeployError):
    pass


# ── .env ──────────────────────────────────────────────────────────────────────

def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (optional `export `, quotes, # comments). Missing file -> {}."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key.strip()] = value
    return out


PLACEHOLDERS = {"dummy", "changeme", "change-me", "todo", "xxx", "none", "null", "your-key-here"}


def token_value(env: dict[str, str], name: str) -> str:
    """The token, or "" when unset or an obvious placeholder (so `dummy` is never deployed)."""

    def real(key: str) -> str:
        v = env.get(key, "").strip()
        return "" if v.lower() in PLACEHOLDERS or v.startswith("<") else v

    # Deliberately no fallback from RADAR_GITHUB_TOKEN to GITHUB_TOKEN: locally GITHUB_TOKEN is often a
    # broad-scope token (e.g. copied from `gh auth token`). Only a dedicated, read-only fine-grained token
    # may be shipped to Render and GitHub Actions.
    return real(name)


def check_tokens(env: dict[str, str], which: Callable[[str], str | None] = shutil.which,
                 live: bool = False) -> tuple[bool, list[str]]:
    """Validate that every required token and CLI exists. Returns (ok, human-readable lines)."""
    ok, lines = True, []
    for t in TOKENS:
        if t.live_only and not live:
            continue
        if token_value(env, t.name):
            lines.append(f"  ok       {t.name}")
            continue
        ok = ok and not t.required
        tag = "MISSING " if t.required else "optional"
        lines.append(f"  {tag} {t.name}\n             create it: {t.where}\n             {t.how}")
    for tool, hint in (("gh", "install https://cli.github.com and run `gh auth login`"),
                       ("npx", "install Node.js 20+ (https://nodejs.org)"),
                       ("docker", "install Docker and run `docker compose up -d db` (the seed step uses it)")):
        if which(tool):
            lines.append(f"  ok       `{tool}` on PATH")
        else:
            ok = False
            lines.append(f"  MISSING  `{tool}`: {hint}")
    return ok, lines


# ── context: redaction, HTTP, subprocess, state ───────────────────────────────

@dataclass
class Ctx:
    env: dict[str, str]
    dry_run: bool = False
    live: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    transport: httpx.BaseTransport | None = None
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
    sleep: Callable[[float], None] = time.sleep
    out: Callable[[str], None] = print
    root: Path = ROOT
    secrets: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        for t in TOKENS:
            self.secret(token_value(self.env, t.name))
        self.secret(self.env.get("PROD_ADMIN_TOKEN", ""))

    @property
    def kinds(self) -> tuple[str, ...]:
        """Backends this deploy manages: the snapshot demo always, the live one with --live."""
        return ("live", "demo") if self.live else ("demo",)

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def state_file(self) -> Path:
        return self.root / "deploy" / "state.json"

    @property
    def doc_file(self) -> Path:
        return self.root / "docs" / "DEPLOYMENT.md"

    def secret(self, value: str | None) -> str:
        if value and len(value) >= 6:
            self.secrets.add(value)
        return value or ""

    def redact(self, text: str) -> str:
        for s in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        return re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", text)  # any password in a URL

    def log(self, msg: str) -> None:
        self.out(self.redact(msg))

    def token(self, name: str) -> str:
        value = token_value(self.env, name)
        if value:
            return value
        if self.dry_run:
            return f"<{name}>"
        raise DeployError(f"{name} is missing from .env; run `make deploy-check` for where to create it.")

    def run(self, args: list[str], *, input: str | None = None, env: dict[str, str] | None = None,
            fake_stdout: str = "", cwd: Path | None = None, stdin_note: str = "value on stdin") -> subprocess.CompletedProcess:
        self.log("  $ " + " ".join(shlex.quote(a) for a in args) + (f"   # {stdin_note}" if input else ""))
        if self.dry_run:
            return subprocess.CompletedProcess(args, 0, fake_stdout, "")
        proc = self.runner(args, input=input, env=env, capture_output=True, text=True, cwd=str(cwd or self.root))
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            raise DeployError(f"`{' '.join(args[:3])}` failed (exit {proc.returncode}):\n{self.redact(tail)}")
        return proc

    def save_state(self) -> None:
        if self.dry_run:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class Api:
    """Tiny JSON client: bearer auth, retries on 423/429/5xx, redacted logging, readable errors."""

    RETRY = (423, 429, 500, 502, 503, 504)

    def __init__(self, ctx: Ctx, name: str, base: str, token: str, params: dict[str, str] | None = None):
        self.ctx, self.name, self.base = ctx, name, base
        self.params = dict(params or {})
        self.client = httpx.Client(base_url=base, timeout=60, transport=ctx.transport,
                                   headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def __call__(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None,
                 allow: tuple[int, ...] = ()) -> httpx.Response:
        query = {**self.params, **(params or {})}
        shown = f"  {method} {self.base}{path}" + (f"?{httpx.QueryParams(query)}" if query else "")
        if self.ctx.dry_run and json is not None:
            shown += "\n      " + _dumps(json)
        self.ctx.log(shown)
        for attempt in range(6):
            resp = self.client.request(method, path, params=query or None, json=json)
            # 423 (Neon: operation in progress) and 429 are safe to retry; 5xx only for reads (no duplicate creates).
            retry = resp.status_code in (423, 429) or (method == "GET" and resp.status_code in self.RETRY)
            if not retry or attempt == 5:
                break
            self.ctx.sleep(min(2 ** attempt * 2, 30))
        if resp.is_success or resp.status_code in allow:
            return resp
        raise DeployError(f"{self.name}: {method} {path} -> HTTP {resp.status_code}: "
                          f"{self.ctx.redact(resp.text[:600])}")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(", ", ": "))


# ── 1. Neon ───────────────────────────────────────────────────────────────────

def neon_step(ctx: Ctx) -> dict[str, dict[str, str]]:
    """Create or reuse the project and the databases; return {demo[, live]: {pooled, direct}} URIs."""
    ctx.log("\n== Neon: project, databases, connection strings")
    api = Api(ctx, "Neon", NEON_API, ctx.token("NEON_API_KEY"))
    org_id = neon_org_id(ctx, api)
    project = neon_find_project(api, org_id)
    if project:
        project_id = project["id"]
        ctx.log(f"  reuse project {NEON_PROJECT} ({project_id}, {project.get('region_id', '?')})")
    else:
        body: dict[str, Any] = {"name": NEON_PROJECT, "region_id": NEON_REGION, "pg_version": NEON_PG_VERSION}
        if org_id:
            body["org_id"] = org_id
        created = api("POST", "/projects", json={"project": body}).json()
        project_id = created["project"]["id"]
        ctx.log(f"  created project {NEON_PROJECT} ({project_id}) in {NEON_REGION}")
        neon_wait(ctx, api, project_id, created.get("operations"))

    branches = api("GET", f"/projects/{project_id}/branches").json().get("branches", [])
    branch = next((b for b in branches if b.get("default")), branches[0] if branches else None)
    if not branch:
        raise DeployError(f"Neon project {project_id} has no branch")
    branch_id = branch["id"]
    roles = api("GET", f"/projects/{project_id}/branches/{branch_id}/roles").json().get("roles", [])
    role = next((r["name"] for r in roles if r["name"] == "neondb_owner"),
                next((r["name"] for r in roles if not r.get("protected")), None))
    if not role:
        raise DeployError(f"Neon project {project_id} has no usable role")

    existing = {d["name"] for d in
                api("GET", f"/projects/{project_id}/branches/{branch_id}/databases").json().get("databases", [])}
    wanted = {kind: DATABASES[kind] for kind in ctx.kinds}
    for db in wanted.values():
        if db in existing:
            ctx.log(f"  reuse database {db}")
            continue
        resp = api("POST", f"/projects/{project_id}/branches/{branch_id}/databases",
                   json={"database": {"name": db, "owner_name": role}}).json()
        ctx.log(f"  created database {db} (owner {role})")
        neon_wait(ctx, api, project_id, resp.get("operations"))

    uris: dict[str, dict[str, str]] = {}
    for kind, db in wanted.items():
        uris[kind] = {}
        for label, pooled in (("pooled", "true"), ("direct", "false")):
            uri = api("GET", f"/projects/{project_id}/connection_uri",
                      params={"branch_id": branch_id, "database_name": db, "role_name": role,
                              "pooled": pooled}).json()["uri"]
            ctx.secret(urlsplit(uri).password)
            uris[kind][label] = uri
    ctx.state["neon"] = {"project_id": project_id, "branch_id": branch_id, "org_id": org_id, "role": role,
                         "region": NEON_REGION, "databases": list(wanted.values())}
    ctx.save_state()
    return uris


def neon_org_id(ctx: Ctx, api: Api) -> str | None:
    """Personal API keys must name an organization (NEON_ORG_ID, or the only one the key can see)."""
    if ctx.env.get("NEON_ORG_ID"):
        return ctx.env["NEON_ORG_ID"]
    resp = api("GET", "/users/me/organizations", allow=(401, 403, 404))
    orgs = (resp.json().get("organizations") or []) if resp.is_success else []
    if len(orgs) == 1:
        return orgs[0]["id"]
    if len(orgs) > 1:
        listing = ", ".join(f"{o['id']} ({o.get('name', '?')})" for o in orgs)
        raise DeployError(f"Your Neon key sees several organizations: {listing}. Add NEON_ORG_ID=<id> to .env.")
    return None


def neon_find_project(api: Api, org_id: str | None) -> dict[str, Any] | None:
    params: dict[str, Any] = {"search": NEON_PROJECT, "limit": 100}
    if org_id:
        params["org_id"] = org_id
    projects = api("GET", "/projects", params=params).json().get("projects", [])
    return next((p for p in projects if p.get("name") == NEON_PROJECT), None)


def neon_wait(ctx: Ctx, api: Api, project_id: str, operations: list[dict[str, Any]] | None) -> None:
    for op in operations or []:
        for _ in range(150):
            status = op.get("status")
            if status in ("finished", "skipped", "cancelled"):
                break
            if status in ("failed", "error"):
                raise DeployError(f"Neon operation {op.get('action')} failed: {op.get('error', '')}")
            ctx.sleep(2)
            op = api("GET", f"/projects/{project_id}/operations/{op['id']}").json()["operation"]
        else:
            raise DeployError(f"Neon operation {op.get('id')} did not finish in time")


# ── 2. Seed ───────────────────────────────────────────────────────────────────

LOCAL_DB_NAME = "radar_demo"
LOCAL_DB_URL = f"postgresql+psycopg://radar:radar@localhost:5432/{LOCAL_DB_NAME}"  # docker-compose `db`
COMPOSE_DB = ["docker", "compose", "exec", "-T", "db"]
# The replay is fully offline and deterministic: no LLM, no providers, clock pinned to the snapshot date.
SEED_ENV = {"DATABASE_URL": LOCAL_DB_URL, "DATABASE_URL_DIRECT": LOCAL_DB_URL, "DEMO_SNAPSHOT": "true",
            "DEMO_SYNTHETIC": "false", "LLM_PROVIDER": "none", "ANTHROPIC_API_KEY": "", "RADAR_NULLPOOL": "1"}


def seed_step(ctx: Ctx, dbs: dict[str, dict[str, str]]) -> None:
    """Replay the committed snapshot into the local docker-compose Postgres, then copy that database into Neon.

    The replay (ingest + scoring + reconstructed history) takes minutes on a laptop; over the network or on
    Render's 0.1-CPU free instance it would take far longer, so the backend's own SEED_ON_START stays a fallback.
    """
    ctx.log(f"\n== Seed: verified snapshot -> local {LOCAL_DB_NAME} -> Neon radar_demo")
    exists = f"SELECT 1 FROM pg_database WHERE datname = '{LOCAL_DB_NAME}'"
    ctx.run([*COMPOSE_DB, "sh", "-c",
             f'psql -U radar -d postgres -tAc "{exists}" | grep -q 1 || createdb -U radar {LOCAL_DB_NAME}'])
    env, backend = {**os.environ, **SEED_ENV}, ctx.root / "backend"
    ctx.run(["uv", "run", "alembic", "upgrade", "head"], env=env, cwd=backend)
    ctx.log("  replaying the snapshot locally (a few minutes) ...")
    ctx.run(["uv", "run", "radar", "seed-demo", "--reset", "--no-fictional"], env=env, cwd=backend)
    dump = ctx.run([*COMPOSE_DB, "pg_dump", "-U", "radar", "-d", LOCAL_DB_NAME, "--no-owner", "--no-privileges",
                    "--no-comments", "--clean", "--if-exists"], fake_stdout="-- pg_dump (dry run)\n")
    ctx.log(f"  restoring {len(dump.stdout or '') // 1024} KB of SQL into Neon radar_demo ...")
    ctx.run([*COMPOSE_DB, "psql", libpq_url(dbs["demo"]["direct"]), "-q", "-v", "ON_ERROR_STOP=1",
             "-c", "SET client_min_messages = warning", "-f", "-"],
            input=dump.stdout, stdin_note="the dump on stdin")
    ctx.state["seeded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ctx.save_state()


def libpq_url(uri: str) -> str:
    """psql wants postgresql://..., not SQLAlchemy's postgresql+psycopg://..."""
    return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql://", uri)


# ── 3. Render ─────────────────────────────────────────────────────────────────

def render_env(ctx: Ctx, kind: str, db: dict[str, str], frontend_url: str) -> dict[str, str]:
    """Environment for one service (radar-live = live data; radar-demo = verified snapshot)."""
    env = {
        "DATABASE_URL": db["pooled"],
        "DATABASE_URL_DIRECT": db["direct"],
        "FRONTEND_URL": frontend_url,
        "TRUST_PROXY_CLIENT_IP": "true",
        "DEMO_SYNTHETIC": "false",
        "DB_POOL_SIZE": "3",
        "DB_MAX_OVERFLOW": "2",
    }
    if kind == "live":
        anthropic = token_value(ctx.env, "PROD_ANTHROPIC_API_KEY")
        env.update(APP_ENV="production", DEMO_SNAPSHOT="false", SEED_ON_START="none",
                   ADMIN_TOKEN=admin_token(ctx), GITHUB_TOKEN=ctx.token("RADAR_GITHUB_TOKEN"),
                   LLM_PROVIDER="anthropic" if anthropic else "none", LLM_DAILY_USD_CAP="3")
        if anthropic:
            env["ANTHROPIC_API_KEY"] = anthropic
    else:
        env.update(APP_ENV="demo", DEMO_SNAPSHOT="true", SEED_ON_START="snapshot", LLM_PROVIDER="none")
        if LOCK_DEMO_WRITES:
            env["ADMIN_TOKEN"] = admin_token(ctx)
    return env


def render_service_payload(name: str, owner_id: str, env: dict[str, str]) -> dict[str, Any]:
    """POST /v1/services body: a free Docker web service built from backend/Dockerfile with repo-root context."""
    return {
        "type": "web_service",
        "name": name,
        "ownerId": owner_id,
        "repo": REPO_URL,
        "branch": BRANCH,
        "autoDeploy": "yes",
        "envVars": [{"key": k, "value": v} for k, v in env.items()],
        "serviceDetails": {
            "runtime": "docker",
            "plan": "free",
            "region": RENDER_REGION,
            "healthCheckPath": "/api/health",
            "numInstances": 1,
            "envSpecificDetails": {"dockerfilePath": "./backend/Dockerfile", "dockerContext": "."},
        },
    }


def admin_token(ctx: Ctx) -> str:
    """PROD_ADMIN_TOKEN from .env, generated once (48 hex chars) and appended to .env if missing."""
    token = ctx.env.get("PROD_ADMIN_TOKEN", "").strip()
    if token:
        return token
    token = ctx.secret(secrets.token_hex(24))
    ctx.env["PROD_ADMIN_TOKEN"] = token
    if not ctx.dry_run:
        with ctx.env_file.open("a") as f:
            f.write("\n# Admin token of the deployed radar-live backend (scripts/deploy.py). Paste it at /unlock.\n"
                    f"PROD_ADMIN_TOKEN={token}\n")
        ctx.log("  generated PROD_ADMIN_TOKEN and saved it to .env")
    return token


def render_step(ctx: Ctx, dbs: dict[str, dict[str, str]]) -> dict[str, str]:
    ctx.log(f"\n== Render: {' + '.join(SERVICES[k] for k in ctx.kinds)} (free, Docker, oregon)")
    api = Api(ctx, "Render", RENDER_API, ctx.token("RENDER_API_KEY"))
    owner_id = render_owner(ctx, api)
    frontend = ctx.state.get("frontend_url") or PROVISIONAL_FRONTEND_URL
    services = ctx.state.setdefault("render", {})
    waits: list[tuple[str, str, str | None]] = []
    for kind in ctx.kinds:
        name = SERVICES[kind]
        desired = render_env(ctx, kind, dbs[kind], frontend)
        svc = render_find_service(api, owner_id, name)
        if svc is None:
            resp = api("POST", "/services", json=render_service_payload(name, owner_id, desired),
                       allow=(400, 401, 403, 404, 409, 422))
            if not resp.is_success:
                raise_render_create_error(ctx, name, resp)
            body = resp.json()
            svc, deploy_id = body["service"], body.get("deployId")
            ctx.log(f"  created {name} ({svc['id']})")
        else:
            ctx.log(f"  reuse {name} ({svc['id']})")
            deploy_id = render_sync_env(ctx, api, svc["id"], desired)
        url = (svc.get("serviceDetails") or {}).get("url") or f"https://{name}.onrender.com"
        services[kind] = {"id": svc["id"], "name": name, "url": url.rstrip("/")}
        ctx.save_state()
        waits.append((name, svc["id"], deploy_id))
    for name, service_id, deploy_id in waits:
        render_wait(ctx, api, name, service_id, deploy_id)
    return {k: services[k]["url"] for k in ctx.kinds}


def raise_render_create_error(ctx: Ctx, name: str, resp: httpx.Response) -> None:
    text = ctx.redact(resp.text[:600])
    if resp.status_code in (400, 403, 404, 422) and re.search(r"repo|git|github|access", text, re.I):
        raise RepoAccessError(f"Render refused to create {name} (HTTP {resp.status_code}: {text})\n\n{RENDER_FIX}")
    raise DeployError(f"Render: creating {name} failed (HTTP {resp.status_code}): {text}")


def render_owner(ctx: Ctx, api: Api) -> str:
    if ctx.env.get("RENDER_OWNER_ID"):
        return ctx.env["RENDER_OWNER_ID"]
    owners = [o["owner"] for o in api("GET", "/owners", params={"limit": 20}).json()]
    if not owners:
        raise DeployError("Render: the API key sees no workspace")
    if len(owners) > 1:
        ctx.log(f"  several Render workspaces; using {owners[0].get('name')} (set RENDER_OWNER_ID to choose)")
    return owners[0]["id"]


def render_find_service(api: Api, owner_id: str, name: str) -> dict[str, Any] | None:
    rows = api("GET", "/services", params={"name": name, "ownerId": owner_id, "limit": 20}).json()
    return next((r["service"] for r in rows if r["service"].get("name") == name), None)


def render_sync_env(ctx: Ctx, api: Api, service_id: str, desired: dict[str, str]) -> str | None:
    """Upsert only the keys that differ (never deletes vars added in the dashboard). Returns a deploy id to wait
    for: a new deploy when something changed, else an in-flight one, else None."""
    current = {r["envVar"]["key"]: r["envVar"]["value"]
               for r in api("GET", f"/services/{service_id}/env-vars", params={"limit": 100}).json()}
    changed = [k for k, v in desired.items() if current.get(k) != v]
    for key in changed:
        api("PUT", f"/services/{service_id}/env-vars/{key}", json={"value": desired[key]})
    if changed:
        ctx.log(f"  updated env: {', '.join(changed)} -> redeploy")
        return api("POST", f"/services/{service_id}/deploys", json={"clearCache": "do_not_clear"}).json().get("id")
    latest = api("GET", f"/services/{service_id}/deploys", params={"limit": 1}).json()
    status = latest[0]["deploy"]["status"] if latest else None
    if status in DEPLOY_FAILED or status is None:
        ctx.log(f"  last deploy is {status or 'missing'} -> redeploy")
        return api("POST", f"/services/{service_id}/deploys", json={"clearCache": "do_not_clear"}).json().get("id")
    if status in DEPLOY_DONE:
        ctx.log("  env unchanged and live; nothing to do")
        return None
    return latest[0]["deploy"]["id"]


def render_wait(ctx: Ctx, api: Api, name: str, service_id: str, deploy_id: str | None,
                timeout_s: int = 30 * 60) -> None:
    if not deploy_id:
        return
    ctx.log(f"  waiting for {name} deploy {deploy_id} (Docker build on the free tier: ~5-10 min)")
    waited, last = 0, None
    while True:
        status = api("GET", f"/services/{service_id}/deploys/{deploy_id}").json().get("status")
        if status != last:
            ctx.log(f"    {name}: {status}")
            last = status
        if status in DEPLOY_DONE:
            return
        if status in DEPLOY_FAILED:
            raise DeployError(f"Render deploy of {name} ended as {status}. Logs: "
                              f"https://dashboard.render.com/web/{service_id}/deploys/{deploy_id}")
        if waited >= timeout_s:
            raise DeployError(f"Render deploy of {name} still {status} after {timeout_s // 60} min; re-run later")
        ctx.sleep(15)
        waited += 15


# ── 4. Vercel ─────────────────────────────────────────────────────────────────

def vercel_step(ctx: Ctx) -> str:
    ctx.log("\n== Vercel: project, env, production deploy of frontend/")
    render = ctx.state.get("render") or {}
    if not set(ctx.kinds) <= render.keys():
        raise DeployError("No Render URLs in deploy/state.json yet: run `--step render` first.")
    main_url = render["live" if ctx.live else "demo"]["url"]
    token = ctx.token("VERCEL_TOKEN")
    api = Api(ctx, "Vercel", VERCEL_API, token)
    user = api("GET", "/v2/user").json()["user"]
    team_id = ctx.env.get("VERCEL_TEAM_ID") or user.get("defaultTeamId")
    if team_id:
        api.params["teamId"] = team_id

    resp = api("GET", f"/v9/projects/{VERCEL_PROJECT}", allow=(404,))
    if resp.status_code == 404:
        project = api("POST", "/v11/projects", json={"name": VERCEL_PROJECT, "framework": "nextjs"}).json()
        ctx.log(f"  created project {VERCEL_PROJECT} ({project['id']})")
    else:
        project = resp.json()
        ctx.log(f"  reuse project {VERCEL_PROJECT} ({project['id']})")
    # The CLI uploads frontend/ itself, so the project's Root Directory must be empty (else ".../frontend/frontend").
    if project.get("rootDirectory") or project.get("framework") != "nextjs":
        api("PATCH", f"/v9/projects/{project['id']}", json={"rootDirectory": None, "framework": "nextjs"})
        ctx.log("  set framework=nextjs, rootDirectory=(empty): the CLI deploys frontend/ directly")

    api("POST", f"/v10/projects/{project['id']}/env", params={"upsert": "true"},
        json=vercel_env(main_url, render["demo"]["url"], ctx.live))
    ctx.log(f"  env BACKEND_URL, DEMO_BACKEND_URL, APP_ENV, DATA_SOURCE={'auto' if ctx.live else 'snapshot'} (production + preview)")

    org_id = team_id or user["id"]
    ctx.state["vercel"] = {"project_id": project["id"], "org_id": org_id}
    ctx.save_state()
    ctx.log("  building on Vercel (1-3 min) ...")
    proc = ctx.run(["npx", "--yes", "vercel@latest", "deploy", "--prod", "--yes", "--token", token,
                    "--cwd", str(ctx.root / "frontend")],
                   env={**os.environ, "VERCEL_ORG_ID": org_id, "VERCEL_PROJECT_ID": project["id"]},
                   fake_stdout=f"https://{VERCEL_PROJECT}-dryrun.vercel.app\n")
    deployment_urls = re.findall(r"https://[^\s]+\.vercel\.app", proc.stdout or "")
    domains = api("GET", f"/v9/projects/{project['id']}/domains", params={"production": "true"}).json()
    frontend = pick_production_url(domains.get("domains", []), deployment_urls[-1] if deployment_urls else None)
    if not frontend:
        raise DeployError("Vercel deploy finished but no production URL was found")
    ctx.state["frontend_url"] = frontend
    ctx.save_state()
    ctx.log(f"  production URL: {frontend}")

    ctx.log("  FRONTEND_URL on the Render service(s)")
    rapi = Api(ctx, "Render", RENDER_API, ctx.token("RENDER_API_KEY"))
    waits = [(render[k]["name"], render[k]["id"],
              render_sync_env(ctx, rapi, render[k]["id"], {"FRONTEND_URL": frontend})) for k in ctx.kinds]
    for name, service_id, deploy_id in waits:
        render_wait(ctx, rapi, name, service_id, deploy_id)
    return frontend


def vercel_env(main_url: str, demo_url: str, live: bool = False) -> list[dict[str, Any]]:
    """Without --live the site serves the verified snapshot bundled with the frontend (DATA_SOURCE=snapshot): no
    request ever waits on a sleeping backend. With --live, BACKEND_URL serves the site and DEMO_BACKEND_URL /demo,
    and the bundled snapshot is the fallback when the live backend does not answer (DATA_SOURCE=auto)."""
    target = ["production", "preview"]
    pairs = (("BACKEND_URL", main_url), ("DEMO_BACKEND_URL", demo_url), ("APP_ENV", "production"),
             ("DATA_SOURCE", "auto" if live else "snapshot"))
    return [{"key": k, "value": v, "type": "encrypted", "target": target} for k, v in pairs]


def pick_production_url(domains: list[dict[str, Any]], fallback: str | None) -> str | None:
    """Prefer a verified, non-redirecting custom domain, then the shortest *.vercel.app alias."""
    usable = [d["name"] for d in domains if d.get("verified", True) and not d.get("redirect")]
    custom = [n for n in usable if not n.endswith(".vercel.app")]
    pool = custom or sorted((n for n in usable if n.endswith(".vercel.app")), key=len)
    if pool:
        return f"https://{pool[0]}"
    return fallback.rstrip("/") if fallback else None


# ── 5. GitHub ─────────────────────────────────────────────────────────────────

def github_step(ctx: Ctx, dbs: dict[str, dict[str, str]] | None) -> None:
    main = "live" if ctx.live else "demo"
    ctx.log(f"\n== GitHub Actions ({GITHUB_REPO}): keep-warm" + (", secrets, first live capture" if ctx.live else ""))
    main_url = (ctx.state.get("render") or {}).get(main, {}).get("url")
    if not main_url:
        raise DeployError(f"No radar-{main} URL in deploy/state.json yet: run `--step render` first.")
    ctx.run(["gh", "variable", "set", "KEEPWARM_URL", "--body", main_url, "--repo", GITHUB_REPO])
    if not ctx.live:
        ctx.log("  scheduled ingestion stays off (radar-ingest only runs on a schedule when RADAR_LIVE=true)")
        return
    if dbs is None:
        raise DeployError("github --live needs the Neon databases")
    values = {
        "DATABASE_URL": dbs["live"]["pooled"],
        "DATABASE_URL_DIRECT": dbs["live"]["direct"],
        "RADAR_GITHUB_TOKEN": ctx.token("RADAR_GITHUB_TOKEN"),
        "ANTHROPIC_API_KEY": token_value(ctx.env, "PROD_ANTHROPIC_API_KEY"),
    }
    for name, value in github_secret_values(values).items():
        ctx.run(["gh", "secret", "set", name, "--repo", GITHUB_REPO], input=value)  # value on stdin, not argv
    ctx.run(["gh", "variable", "set", "RADAR_LIVE", "--body", "true", "--repo", GITHUB_REPO])
    gh = ctx.state.setdefault("github", {})
    if gh.get("first_capture_dispatched"):
        ctx.log("  first capture already dispatched (see the Actions tab)")
    else:
        ctx.run(["gh", "workflow", "run", "radar-ingest.yml", "-f", "job=github", "--repo", GITHUB_REPO])
        gh["first_capture_dispatched"] = True
        ctx.log(f"  dispatched radar-ingest (job=github): {REPO_URL}/actions/workflows/radar-ingest.yml")
    ctx.save_state()


def github_secret_values(values: dict[str, str]) -> dict[str, str]:
    """Secrets to set (empty optional ones are skipped)."""
    return {k: v for k, v in values.items() if v}


# ── 6. Verify ─────────────────────────────────────────────────────────────────

def verify_targets(state: dict[str, Any], kinds: tuple[str, ...] = ("live", "demo")) -> list[tuple[str, str]]:
    render, frontend = state.get("render") or {}, state.get("frontend_url")
    if not (set(kinds) <= render.keys() and frontend):
        raise DeployError("deploy/state.json lacks Render/Vercel URLs: run the render and vercel steps first.")
    return [(f"{SERVICES[k]} /api/ready", f"{render[k]['url']}/api/ready") for k in kinds] + [
        ("frontend /api/proxy/health", f"{frontend}/api/proxy/health"),
        ("frontend /demo", f"{frontend}/demo"),
    ]


def verify_step(ctx: Ctx, attempts: int = 4) -> bool:
    ctx.log("\n== Verify (a sleeping Render backend can take ~60 s to wake)")
    ok = True
    with httpx.Client(timeout=150, transport=ctx.transport, follow_redirects=True) as client:
        for label, url in verify_targets(ctx.state, ctx.kinds):
            status: int | str = "-"
            for attempt in range(attempts):
                ctx.log(f"  GET {url}")
                try:
                    status = client.get(url).status_code
                except httpx.HTTPError as e:
                    status = type(e).__name__
                if status == 200:
                    break
                if attempt < attempts - 1:
                    ctx.sleep(20)
            ctx.log(f"  {'ok  ' if status == 200 else 'FAIL'} {label}: {status}")
            ok = ok and status == 200
    if not ctx.dry_run:
        ctx.state["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if ok else None
        ctx.save_state()
        write_doc_urls(ctx)
    return ok


DOC_START, DOC_END = "<!-- deploy:urls:start -->", "<!-- deploy:urls:end -->"


def doc_url_block(state: dict[str, Any]) -> str:
    render, fe = state.get("render") or {}, state.get("frontend_url")
    if not fe:
        return "_Not deployed yet. Run `make deploy`; this block is filled in automatically._"
    rows = [f"| Frontend | {fe} |", f"| Guided demo | {fe}/demo |"]
    if "live" in render:
        rows.append(f"| Unlock writes (live) | {fe}/unlock |")
    for kind, label in (("live", "radar-live API"), ("demo", "radar-demo API")):
        if kind in render:
            rows.append(f"| {label} | {render[kind]['url']} (health: `/api/health`, ready: `/api/ready`) |")
    stamp = state.get("verified_at")
    tail = f"\n\nLast verified by `scripts/deploy.py`: {stamp}." if stamp else ""
    return "| What | URL |\n|---|---|\n" + "\n".join(rows) + tail


def update_doc(text: str, block: str) -> str:
    """Replace the marked block; if absent, add a 'Production URL' section right after the H1."""
    pattern = re.compile(re.escape(DOC_START) + r".*?" + re.escape(DOC_END), re.S)
    new = f"{DOC_START}\n{block}\n{DOC_END}"
    if pattern.search(text):
        return pattern.sub(lambda _: new, text, count=1)
    head, sep, rest = text.partition("\n")
    return f"{head}{sep}\n## Production URL\n\n{new}\n{rest}"


def write_doc_urls(ctx: Ctx) -> None:
    if ctx.doc_file.exists():
        ctx.doc_file.write_text(update_doc(ctx.doc_file.read_text(), doc_url_block(ctx.state)))
        ctx.log(f"  wrote URLs to {ctx.doc_file.relative_to(ctx.root)}")


# ── dry run: a fake API that records what would be sent ───────────────────────

def dry_run_transport() -> httpx.MockTransport:
    """Answers like a fresh account (nothing exists yet), so the dry run walks the full create path."""

    def handler(req: httpx.Request) -> httpx.Response:
        host, path, method, q = req.url.host, req.url.path, req.method, req.url.params

        def ok(body: Any, status: int = 200) -> httpx.Response:
            return httpx.Response(status, json=body)

        if host == "console.neon.tech":
            if path.endswith("/users/me/organizations"):
                return ok({"organizations": [{"id": "org-dry-run", "name": "you"}]})
            if path.endswith("/connection_uri"):
                pooler = "-pooler" if q.get("pooled") == "true" else ""
                return ok({"uri": f"postgresql://{q['role_name']}:dry-run-password@ep-dry-run{pooler}"
                                  f".us-west-2.aws.neon.tech/{q['database_name']}?sslmode=require"})
            if path.endswith("/projects"):
                return ok({"projects": []}) if method == "GET" else ok(
                    {"project": {"id": "dry-run-project"}, "operations": []}, 201)
            if path.endswith("/branches"):
                return ok({"branches": [{"id": "br-dry-run", "default": True}]})
            if path.endswith("/roles"):
                return ok({"roles": [{"name": "neondb_owner", "protected": False}]})
            if path.endswith("/databases"):
                return ok({"databases": []}) if method == "GET" else ok({"database": {}, "operations": []}, 201)
        if host == "api.render.com":
            if path.endswith("/owners"):
                return ok([{"owner": {"id": "own-dry-run", "name": "you", "type": "user"}}])
            if path.endswith("/services"):
                if method == "GET":
                    return ok([])
                name = json.loads(req.content)["name"]
                return ok({"service": {"id": f"srv-{name}", "name": name,
                                       "serviceDetails": {"url": f"https://{name}.onrender.com"}},
                           "deployId": f"dep-{name}"}, 201)
            if "/env-vars" in path:
                return ok([] if method == "GET" else {})
            if path.endswith("/deploys"):
                return ok({"id": "dep-dry-run", "status": "created"}, 201) if method == "POST" else ok([])
            if "/deploys/" in path:
                return ok({"id": path.rsplit("/", 1)[-1], "status": "live"})
        if host == "api.vercel.com":
            if path == "/v2/user":
                return ok({"user": {"id": "user-dry-run", "username": "you", "defaultTeamId": None}})
            if re.fullmatch(r"/v9/projects/[^/]+", path) and method == "GET":
                return ok({"error": {"code": "not_found"}}, 404)
            if path == "/v11/projects":
                return ok({"id": "prj_dry_run", "name": VERCEL_PROJECT, "framework": "nextjs", "rootDirectory": None})
            if path.endswith("/env"):
                return ok({"created": [], "failed": []}, 201)
            if path.endswith("/domains"):
                return ok({"domains": [{"name": f"{VERCEL_PROJECT}.vercel.app", "verified": True}]})
        return ok({"status": "ok"})  # verify GETs

    return httpx.MockTransport(handler)


# ── main ──────────────────────────────────────────────────────────────────────

def run_steps(ctx: Ctx, steps: list[str]) -> bool:
    missing = sorted({t for s in steps for t in step_tokens(s, ctx.live) if not token_value(ctx.env, t)})
    if missing and not ctx.dry_run:
        raise DeployError(f"Missing in .env: {', '.join(missing)}. Run `make deploy-check` for where to get them.")
    dbs: dict[str, dict[str, str]] | None = None

    def databases() -> dict[str, dict[str, str]]:
        nonlocal dbs
        if dbs is None:
            dbs = neon_step(ctx)
        return dbs

    ok = True
    for step in steps:
        if step == "neon":
            databases()
        elif step == "seed":
            seed_step(ctx, databases())
        elif step == "render":
            render_step(ctx, databases())
        elif step == "vercel":
            vercel_step(ctx)
        elif step == "github":
            github_step(ctx, databases() if ctx.live else None)
        elif step == "verify":
            ok = verify_step(ctx)
    return ok


def summary(ctx: Ctx) -> str:
    s = ctx.state
    render = s.get("render") or {}
    lines = ["", "== Summary" + (" (dry run: nothing was created)" if ctx.dry_run else "")]
    if s.get("frontend_url"):
        lines += [f"  frontend     {s['frontend_url']}", f"  guided demo  {s['frontend_url']}/demo"]
    for kind in ctx.kinds:
        if kind in render:
            lines.append(f"  {render[kind]['name']:<12} {render[kind]['url']}")
    if s.get("neon"):
        lines.append(f"  neon         project {s['neon']['project_id']} ({', '.join(s['neon'].get('databases', []))})")
    if ctx.live:
        if s.get("frontend_url"):
            lines.append(f"  writes       paste PROD_ADMIN_TOKEN from .env at {s['frontend_url']}/unlock")
        lines.append(f"  ingestion    {REPO_URL}/actions (radar-ingest; keepwarm)")
    else:
        lines.append("  mode         demo: the whole site serves the verified snapshot (re-run with --live for live data)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy ANTI RADAR to Neon + Render + Vercel + GitHub Actions ($0).")
    parser.add_argument("--live", action="store_true",
                        help="also deploy the live backend + scheduled ingestion (default: snapshot demo only)")
    parser.add_argument("--check", action="store_true", help="only validate tokens/CLIs in .env (no network)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and every request; no network")
    parser.add_argument("--step", action="append", choices=STEPS, help="run only this step (repeatable)")
    args = parser.parse_args(argv)

    env = {k: v for k, v in os.environ.items() if k in {t.name for t in TOKENS}}
    env.update({k: v for k, v in load_env(ROOT / ".env").items() if v})  # blank .env lines do not override
    if args.check:
        ok, lines = check_tokens(env, live=args.live)
        print("Deploy prerequisites (repo-root .env):")
        print("\n".join(lines))
        print("\nAll set: run `make deploy`." if ok else "\nAdd the missing values to .env, then re-run `make deploy-check`.")
        return 0 if ok else 1

    ctx = Ctx(env=env, dry_run=args.dry_run, live=args.live, state=load_state(ROOT / "deploy" / "state.json"),
              transport=dry_run_transport() if args.dry_run else None)
    steps = [s for s in STEPS if s in (args.step or STEPS)]
    if ctx.dry_run:
        ctx.log(f"DRY RUN ({'live + demo' if ctx.live else 'demo'}): steps {', '.join(steps)}; "
                "responses are simulated (fresh accounts), secrets shown as ***")
    try:
        ok = run_steps(ctx, steps)
    except RepoAccessError as e:
        ctx.log(f"\n{e}")
        return 2
    except DeployError as e:
        ctx.log(f"\nERROR: {e}")
        return 1
    ctx.log(summary(ctx))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
