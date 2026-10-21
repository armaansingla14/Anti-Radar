"""scripts/deploy.py: request shapes, idempotency and redaction, with mocked HTTP (respx). No real calls."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "deploy.py"
_spec = importlib.util.spec_from_file_location("radar_deploy_script", SCRIPT)
deploy = importlib.util.module_from_spec(_spec)
sys.modules["radar_deploy_script"] = deploy
_spec.loader.exec_module(deploy)

ADMIN = "a1" * 24
TOKENS = {
    "NEON_API_KEY": "neon-key-0123456789",
    "RENDER_API_KEY": "rnd_key_0123456789",
    "VERCEL_TOKEN": "vercel-token-0123456789",
    "RADAR_GITHUB_TOKEN": "github_pat_0123456789",
    "PROD_ANTHROPIC_API_KEY": "sk-ant-test-0123456789",
    "PROD_ADMIN_TOKEN": ADMIN,
}
DBS = {
    "live": {"pooled": "postgresql://neondb_owner:pw-live-secret@ep-x-pooler.us-west-2.aws.neon.tech/radar_live",
             "direct": "postgresql://neondb_owner:pw-live-secret@ep-x.us-west-2.aws.neon.tech/radar_live"},
    "demo": {"pooled": "postgresql://neondb_owner:pw-demo-secret@ep-x-pooler.us-west-2.aws.neon.tech/radar_demo",
             "direct": "postgresql://neondb_owner:pw-demo-secret@ep-x.us-west-2.aws.neon.tech/radar_demo"},
}
NEON, RENDER, VERCEL = "console.neon.tech", "api.render.com", "api.vercel.com"


class FakeRunner:
    def __init__(self, stdout: str = ""):
        self.calls: list[dict] = []
        self.stdout = stdout

    def __call__(self, args, **kw):
        self.calls.append({"args": args, **kw})
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def make_ctx(tmp_path: Path, runner: FakeRunner | None = None, live: bool = True, **env):
    lines: list[str] = []
    ctx = deploy.Ctx(env={**TOKENS, **env}, root=tmp_path, sleep=lambda _s: None, out=lines.append,
                     runner=runner or FakeRunner(), live=live)
    for d in DBS.values():
        for uri in d.values():
            ctx.secret(httpx.URL(uri).password)
    return ctx, lines


def body(call) -> object:
    return json.loads(call.request.content)


# ── .env + --check ────────────────────────────────────────────────────────────

def test_load_env_parses_quotes_comments_and_export(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# c\nexport A=1\nB="two words"\nC=3 # trailing\nEMPTY=\nnot a line\n')
    assert deploy.load_env(p) == {"A": "1", "B": "two words", "C": "3", "EMPTY": ""}
    assert deploy.load_env(tmp_path / "missing") == {}


def test_check_lists_each_missing_token_with_where_to_create_it():
    ok, lines = deploy.check_tokens({"NEON_API_KEY": "x" * 20, "PROD_ANTHROPIC_API_KEY": "dummy"},
                                    which=lambda _: "/bin/x", live=True)
    text = "\n".join(lines)
    assert not ok
    assert "ok       NEON_API_KEY" in text
    for name, link in (("RENDER_API_KEY", "dashboard.render.com"), ("VERCEL_TOKEN", "vercel.com/account/settings/tokens"),
                       ("RADAR_GITHUB_TOKEN", "github.com/settings/personal-access-tokens")):
        assert f"MISSING  {name}" in text and link in text
    assert "optional PROD_ANTHROPIC_API_KEY" in text  # placeholder value "dummy" counts as missing


def test_check_passes_with_required_tokens_and_tools():
    ok, lines = deploy.check_tokens({k: v for k, v in TOKENS.items() if k != "PROD_ANTHROPIC_API_KEY"},
                                    which=lambda _: "/usr/bin/tool", live=True)
    assert ok
    ok, lines = deploy.check_tokens(TOKENS, which=lambda t: None if t == "gh" else "/usr/bin/npx")
    assert not ok and any("`gh`" in line for line in lines)
    ok, lines = deploy.check_tokens(TOKENS, which=lambda t: None if t == "docker" else "/usr/bin/x")
    assert not ok and any("`docker`" in line for line in lines)


def test_demo_deploy_needs_only_neon_render_and_vercel():
    three = {k: TOKENS[k] for k in ("NEON_API_KEY", "RENDER_API_KEY", "VERCEL_TOKEN")}
    ok, lines = deploy.check_tokens(three, which=lambda _: "/usr/bin/tool")
    assert ok and not any("RADAR_GITHUB_TOKEN" in line or "ANTHROPIC" in line for line in lines)
    assert not deploy.check_tokens(three, which=lambda _: "/usr/bin/tool", live=True)[0]
    assert all(set(deploy.step_tokens(s, False)) <= three.keys() for s in deploy.STEPS)


def test_redact_hides_tokens_and_url_passwords(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    text = ctx.redact(f"Bearer {TOKENS['RENDER_API_KEY']} {ADMIN} postgresql://u:other-pass@host/db")
    assert TOKENS["RENDER_API_KEY"] not in text and ADMIN not in text and "other-pass" not in text
    assert "postgresql://u:***@host/db" in text


# ── Neon ──────────────────────────────────────────────────────────────────────

def _neon_common(m, *, projects, databases):
    m.get(host=NEON, path="/api/v2/users/me/organizations").respond(json={"organizations": [{"id": "org-1"}]})
    m.get(host=NEON, path="/api/v2/projects").respond(json={"projects": projects})
    m.get(host=NEON, path__regex=r".*/branches$").respond(json={"branches": [{"id": "br-1", "default": True}]})
    m.get(host=NEON, path__regex=r".*/roles$").respond(
        json={"roles": [{"name": "neondb_owner", "protected": False}]})
    m.get(host=NEON, path__regex=r".*/databases$").respond(json={"databases": [{"name": d} for d in databases]})

    def uri(request):
        q = request.url.params
        pooler = "-pooler" if q["pooled"] == "true" else ""
        return httpx.Response(200, json={"uri": f"postgresql://{q['role_name']}:pw-{q['database_name']}-xyz@"
                                                f"ep-1{pooler}.us-west-2.aws.neon.tech/{q['database_name']}"})

    return m.get(host=NEON, path="/api/v2/projects/prj-1/connection_uri").mock(side_effect=uri)


def test_neon_creates_project_and_databases_then_fetches_pooled_and_direct(tmp_path):
    ctx, lines = make_ctx(tmp_path)
    with respx.mock(assert_all_called=True) as m:
        uri_route = _neon_common(m, projects=[], databases=["neondb"])
        create = m.post(host=NEON, path="/api/v2/projects").respond(
            201, json={"project": {"id": "prj-1"}, "operations": [{"id": "op-1", "status": "running"}]})
        m.get(host=NEON, path="/api/v2/projects/prj-1/operations/op-1").respond(
            json={"operation": {"id": "op-1", "status": "finished"}})
        create_db = m.post(host=NEON, path="/api/v2/projects/prj-1/branches/br-1/databases").respond(
            201, json={"database": {}, "operations": []})
        uris = deploy.neon_step(ctx)

    assert body(create.calls[0]) == {"project": {"name": "anti-radar", "region_id": "aws-us-west-2",
                                                 "pg_version": 17, "org_id": "org-1"}}
    assert create.calls[0].request.headers["Authorization"] == f"Bearer {TOKENS['NEON_API_KEY']}"
    assert [body(c)["database"] for c in create_db.calls] == [
        {"name": "radar_live", "owner_name": "neondb_owner"}, {"name": "radar_demo", "owner_name": "neondb_owner"}]
    pooled = [c.request.url.params["pooled"] for c in uri_route.calls]
    assert sorted(pooled) == ["false", "false", "true", "true"]
    assert all(c.request.url.params["branch_id"] == "br-1" for c in uri_route.calls)
    assert "-pooler" in uris["live"]["pooled"] and "-pooler" not in uris["live"]["direct"]
    assert uris["demo"]["direct"].endswith("/radar_demo")
    assert ctx.state["neon"]["project_id"] == "prj-1"
    state = json.loads((tmp_path / "deploy" / "state.json").read_text())
    assert "pw-radar_live-xyz" not in json.dumps(state)  # state never stores DB passwords
    assert not any("pw-radar_live-xyz" in line for line in lines)


def test_neon_rerun_reuses_project_and_databases(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    with respx.mock(assert_all_called=False) as m:
        _neon_common(m, projects=[{"id": "prj-1", "name": "anti-radar"}, {"id": "zzz", "name": "anti-radar-old"}],
                     databases=["neondb", "radar_live", "radar_demo"])
        posts = m.post(host=NEON).respond(500)
        deploy.neon_step(ctx)
    assert not posts.called


def test_neon_demo_only_creates_just_the_demo_database(tmp_path):
    ctx, _ = make_ctx(tmp_path, live=False)
    with respx.mock(assert_all_called=False) as m:
        uri_route = _neon_common(m, projects=[{"id": "prj-1", "name": "anti-radar"}], databases=["neondb"])
        create_db = m.post(host=NEON, path="/api/v2/projects/prj-1/branches/br-1/databases").respond(
            201, json={"database": {}, "operations": []})
        uris = deploy.neon_step(ctx)
    assert [body(c)["database"]["name"] for c in create_db.calls] == ["radar_demo"]
    assert set(uris) == {"demo"} and uri_route.call_count == 2


def test_neon_multiple_orgs_requires_neon_org_id(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    with respx.mock() as m:
        m.get(host=NEON, path="/api/v2/users/me/organizations").respond(
            json={"organizations": [{"id": "org-a", "name": "A"}, {"id": "org-b", "name": "B"}]})
        with pytest.raises(deploy.DeployError, match="NEON_ORG_ID"):
            deploy.neon_step(ctx)


# ── Render ────────────────────────────────────────────────────────────────────

def test_render_payload_is_a_free_docker_web_service_from_the_repo_root():
    p = deploy.render_service_payload("radar-live", "own-1", {"APP_ENV": "production"})
    assert p["type"] == "web_service" and p["ownerId"] == "own-1"
    assert p["repo"] == "https://github.com/armaansingla14/Anti-Radar" and p["branch"] == "main"
    assert p["envVars"] == [{"key": "APP_ENV", "value": "production"}]
    d = p["serviceDetails"]
    assert (d["runtime"], d["plan"], d["region"], d["healthCheckPath"]) == ("docker", "free", "oregon", "/api/health")
    assert d["envSpecificDetails"] == {"dockerfilePath": "./backend/Dockerfile", "dockerContext": "."}


def test_render_env_live_vs_demo(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    live = deploy.render_env(ctx, "live", DBS["live"], "https://fe.vercel.app")
    demo = deploy.render_env(ctx, "demo", DBS["demo"], "https://fe.vercel.app")
    assert live["APP_ENV"] == "production" and live["SEED_ON_START"] == "none" and live["DEMO_SNAPSHOT"] == "false"
    assert live["ADMIN_TOKEN"] == ADMIN and live["GITHUB_TOKEN"] == TOKENS["RADAR_GITHUB_TOKEN"]
    assert live["ANTHROPIC_API_KEY"] == TOKENS["PROD_ANTHROPIC_API_KEY"] and live["LLM_PROVIDER"] == "anthropic"
    assert demo["APP_ENV"] == "demo" and demo["DEMO_SNAPSHOT"] == "true" and demo["SEED_ON_START"] == "snapshot"
    assert demo["LLM_PROVIDER"] == "none"
    assert not {"ANTHROPIC_API_KEY", "GITHUB_TOKEN", "ADMIN_TOKEN"} & demo.keys()
    for env, db in ((live, DBS["live"]), (demo, DBS["demo"])):
        assert env["DATABASE_URL"] == db["pooled"] and env["DATABASE_URL_DIRECT"] == db["direct"]
        assert (env["TRUST_PROXY_CLIENT_IP"], env["DB_POOL_SIZE"], env["DB_MAX_OVERFLOW"]) == ("true", "3", "2")
        assert env["FRONTEND_URL"] == "https://fe.vercel.app"


def test_shell_anthropic_key_is_never_deployed(tmp_path):
    """ANTHROPIC_API_KEY in the shell/.env may be a key for other work: never shipped."""
    ctx, _ = make_ctx(tmp_path, PROD_ANTHROPIC_API_KEY="", ANTHROPIC_API_KEY="sk-ant-otherwork-0123456789")
    live = deploy.render_env(ctx, "live", DBS["live"], "https://fe.vercel.app")
    assert "ANTHROPIC_API_KEY" not in live and live["LLM_PROVIDER"] == "none"
    assert "ANTHROPIC_API_KEY" not in {t.name for t in deploy.TOKENS}  # main() only picks up TOKENS from the shell


def test_admin_token_is_generated_once_into_dotenv_as_prod_admin_token(tmp_path):
    (tmp_path / ".env").write_text("APP_ENV=development\n")
    ctx, _ = make_ctx(tmp_path, PROD_ADMIN_TOKEN="")
    token = deploy.admin_token(ctx)
    assert len(token) == 48 and deploy.admin_token(ctx) == token
    saved = deploy.load_env(tmp_path / ".env")
    assert saved["PROD_ADMIN_TOKEN"] == token and "ADMIN_TOKEN" not in saved  # local dev stays writable


def test_render_creates_both_services_and_waits_for_live(tmp_path):
    ctx, lines = make_ctx(tmp_path)
    with respx.mock(assert_all_called=True) as m:
        m.get(host=RENDER, path="/v1/owners").respond(json=[{"owner": {"id": "own-1", "name": "me"}}])
        m.get(host=RENDER, path="/v1/services").respond(json=[])

        def create(request):
            name = json.loads(request.content)["name"]
            return httpx.Response(201, json={"service": {"id": f"srv-{name}", "name": name,
                                                         "serviceDetails": {"url": f"https://{name}-x1.onrender.com"}},
                                             "deployId": f"dep-{name}"})

        created = m.post(host=RENDER, path="/v1/services").mock(side_effect=create)
        polls = m.get(host=RENDER, path__regex=r"/v1/services/srv-[a-z-]+/deploys/dep-[a-z-]+").mock(
            side_effect=[httpx.Response(200, json={"status": "build_in_progress"}),
                         httpx.Response(200, json={"status": "live"}),
                         httpx.Response(200, json={"status": "live"})])
        urls = deploy.render_step(ctx, DBS)

    assert [body(c)["name"] for c in created.calls] == ["radar-live", "radar-demo"]
    live_env = {e["key"]: e["value"] for e in body(created.calls[0])["envVars"]}
    assert live_env["FRONTEND_URL"] == deploy.PROVISIONAL_FRONTEND_URL  # https, so config_check passes
    assert polls.call_count == 3
    assert urls == {"live": "https://radar-live-x1.onrender.com", "demo": "https://radar-demo-x1.onrender.com"}
    assert ctx.state["render"]["live"]["id"] == "srv-radar-live"
    out = "\n".join(lines)
    assert ADMIN not in out and TOKENS["RADAR_GITHUB_TOKEN"] not in out


def test_render_demo_only_creates_just_radar_demo(tmp_path):
    ctx, _ = make_ctx(tmp_path, live=False)
    with respx.mock(assert_all_called=True) as m:
        m.get(host=RENDER, path="/v1/owners").respond(json=[{"owner": {"id": "own-1", "name": "me"}}])
        m.get(host=RENDER, path="/v1/services").respond(json=[])
        created = m.post(host=RENDER, path="/v1/services").respond(201, json={
            "service": {"id": "srv-d", "name": "radar-demo", "serviceDetails": {"url": "https://radar-demo.onrender.com"}},
            "deployId": "dep-d"})
        m.get(host=RENDER, path="/v1/services/srv-d/deploys/dep-d").respond(json={"status": "live"})
        assert deploy.render_step(ctx, {"demo": DBS["demo"]}) == {"demo": "https://radar-demo.onrender.com"}
    env = {e["key"]: e["value"] for e in body(created.calls[0])["envVars"]}
    assert body(created.calls[0])["name"] == "radar-demo" and env["DEMO_SNAPSHOT"] == "true"
    assert not {"ADMIN_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"} & env.keys()


def test_render_repo_not_accessible_prints_one_click_fix(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    with respx.mock() as m:
        m.get(host=RENDER, path="/v1/owners").respond(json=[{"owner": {"id": "own-1"}}])
        m.get(host=RENDER, path="/v1/services").respond(json=[])
        m.post(host=RENDER, path="/v1/services").respond(400, json={"message": "invalid repo: could not access repo"})
        with pytest.raises(deploy.RepoAccessError) as err:
            deploy.render_step(ctx, DBS)
    assert "github.com/apps/render/installations/new" in str(err.value)
    assert "armaansingla14/Anti-Radar" in str(err.value)


def test_render_rerun_updates_only_changed_env_and_skips_deploy_when_live(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    desired = {"APP_ENV": "production", "FRONTEND_URL": "https://new.vercel.app"}
    with respx.mock(assert_all_called=False) as m:
        m.get(host=RENDER, path="/v1/services/srv-1/env-vars").respond(json=[
            {"envVar": {"key": "APP_ENV", "value": "production"}},
            {"envVar": {"key": "FRONTEND_URL", "value": "https://old.vercel.app"}},
            {"envVar": {"key": "X_BEARER_TOKEN", "value": "manual"}}])
        put = m.put(host=RENDER, path__regex=r"/v1/services/srv-1/env-vars/.+").respond(json={})
        dep = m.post(host=RENDER, path="/v1/services/srv-1/deploys").respond(201, json={"id": "dep-9"})
        assert deploy.render_sync_env(ctx, deploy.Api(ctx, "Render", deploy.RENDER_API, "k"), "srv-1", desired) == "dep-9"
        assert [c.request.url.path.rsplit("/", 1)[-1] for c in put.calls] == ["FRONTEND_URL"]
        assert body(put.calls[0]) == {"value": "https://new.vercel.app"}
        assert dep.call_count == 1

    with respx.mock(assert_all_called=False) as m:
        m.get(host=RENDER, path="/v1/services/srv-1/env-vars").respond(
            json=[{"envVar": {"key": k, "value": v}} for k, v in desired.items()])
        m.get(host=RENDER, path="/v1/services/srv-1/deploys").respond(json=[{"deploy": {"id": "d", "status": "live"}}])
        dep = m.post(host=RENDER, path="/v1/services/srv-1/deploys").respond(201, json={"id": "x"})
        assert deploy.render_sync_env(ctx, deploy.Api(ctx, "Render", deploy.RENDER_API, "k"), "srv-1", desired) is None
        assert not dep.called


def test_render_failed_deploy_points_at_logs(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    with respx.mock() as m:
        m.get(host=RENDER, path="/v1/services/srv-1/deploys/dep-1").respond(json={"status": "build_failed"})
        with pytest.raises(deploy.DeployError, match="dashboard.render.com/web/srv-1/deploys/dep-1"):
            deploy.render_wait(ctx, deploy.Api(ctx, "Render", deploy.RENDER_API, "k"), "radar-live", "srv-1", "dep-1")


# ── Vercel ────────────────────────────────────────────────────────────────────

def _render_state():
    return {"live": {"id": "srv-l", "name": "radar-live", "url": "https://radar-live.onrender.com"},
            "demo": {"id": "srv-d", "name": "radar-demo", "url": "https://radar-demo.onrender.com"}}


def test_vercel_links_project_sets_env_deploys_and_updates_frontend_url(tmp_path):
    runner = FakeRunner(stdout="https://anti-radar-abc123-me.vercel.app\n")
    ctx, lines = make_ctx(tmp_path, runner=runner)
    ctx.state["render"] = _render_state()
    with respx.mock(assert_all_called=True) as m:
        m.get(host=VERCEL, path="/v2/user").respond(json={"user": {"id": "u-1", "defaultTeamId": "team_1"}})
        m.get(host=VERCEL, path="/v9/projects/anti-radar").respond(
            json={"id": "prj_1", "framework": "nextjs", "rootDirectory": "frontend"})
        patch = m.patch(host=VERCEL, path="/v9/projects/prj_1").respond(json={})
        env = m.post(host=VERCEL, path="/v10/projects/prj_1/env").respond(201, json={"created": [], "failed": []})
        m.get(host=VERCEL, path="/v9/projects/prj_1/domains").respond(json={"domains": [
            {"name": "anti-radar-me.vercel.app", "verified": True},
            {"name": "anti-radar.vercel.app", "verified": True}]})
        m.get(host=RENDER, path__regex=r"/v1/services/srv-[ld]/env-vars").respond(
            json=[{"envVar": {"key": "FRONTEND_URL", "value": deploy.PROVISIONAL_FRONTEND_URL}}])
        m.get(host=RENDER, path__regex=r"/v1/services/srv-[ld]/deploys").respond(
            json=[{"deploy": {"id": "d", "status": "live"}}])
        url = deploy.vercel_step(ctx)

    assert url == "https://anti-radar.vercel.app"  # unchanged from the provisional value -> no Render redeploy
    assert body(patch.calls[0]) == {"rootDirectory": None, "framework": "nextjs"}
    assert env.calls[0].request.url.params["upsert"] == "true"
    assert env.calls[0].request.url.params["teamId"] == "team_1"
    sent = {e["key"]: e for e in body(env.calls[0])}
    assert sent["BACKEND_URL"]["value"] == "https://radar-live.onrender.com"
    assert sent["DEMO_BACKEND_URL"]["value"] == "https://radar-demo.onrender.com"
    assert sent["APP_ENV"]["value"] == "production" and sent["APP_ENV"]["target"] == ["production", "preview"]
    assert sent["DATA_SOURCE"]["value"] == "auto"  # --live: live backend first, bundled snapshot as fallback
    (call,) = runner.calls
    assert call["args"][:6] == ["npx", "--yes", "vercel@latest", "deploy", "--prod", "--yes"]
    assert call["args"][call["args"].index("--cwd") + 1].endswith("frontend")
    assert call["env"]["VERCEL_ORG_ID"] == "team_1" and call["env"]["VERCEL_PROJECT_ID"] == "prj_1"
    assert not any(TOKENS["VERCEL_TOKEN"] in line for line in lines)
    assert ctx.state["frontend_url"] == url


def test_vercel_demo_only_points_the_whole_site_at_the_snapshot_backend(tmp_path):
    runner = FakeRunner(stdout="https://anti-radar-abc123-me.vercel.app\n")
    ctx, _ = make_ctx(tmp_path, runner=runner, live=False)
    ctx.state["render"] = {"demo": _render_state()["demo"]}
    with respx.mock(assert_all_called=False) as m:
        m.get(host=VERCEL, path="/v2/user").respond(json={"user": {"id": "u-1"}})
        m.get(host=VERCEL, path="/v9/projects/anti-radar").respond(json={"id": "prj_1", "framework": "nextjs"})
        env = m.post(host=VERCEL, path="/v10/projects/prj_1/env").respond(201, json={})
        m.get(host=VERCEL, path="/v9/projects/prj_1/domains").respond(json={"domains": [{"name": "anti-radar.vercel.app"}]})
        m.get(host=RENDER, path="/v1/services/srv-d/env-vars").respond(
            json=[{"envVar": {"key": "FRONTEND_URL", "value": deploy.PROVISIONAL_FRONTEND_URL}}])
        m.get(host=RENDER, path="/v1/services/srv-d/deploys").respond(json=[{"deploy": {"id": "d", "status": "live"}}])
        deploy.vercel_step(ctx)
    sent = {e["key"]: e["value"] for e in body(env.calls[0])}
    assert sent["BACKEND_URL"] == sent["DEMO_BACKEND_URL"] == "https://radar-demo.onrender.com"
    assert sent["DATA_SOURCE"] == "snapshot"  # the public site never waits on a sleeping backend


def test_vercel_requires_render_urls(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    with pytest.raises(deploy.DeployError, match="--step render"):
        deploy.vercel_step(ctx)


def test_pick_production_url():
    pick = deploy.pick_production_url
    assert pick([{"name": "anti-radar-x.vercel.app"}, {"name": "anti-radar.vercel.app"}], None) == \
        "https://anti-radar.vercel.app"
    assert pick([{"name": "radar.example.com", "verified": True}, {"name": "a.vercel.app"}], None) == \
        "https://radar.example.com"
    assert pick([{"name": "www.x.com", "redirect": "x.com"}], "https://dep.vercel.app/") == "https://dep.vercel.app"
    assert pick([], None) is None


# ── GitHub ────────────────────────────────────────────────────────────────────

def test_github_sets_secrets_via_stdin_and_dispatches_first_capture_once(tmp_path):
    runner = FakeRunner()
    ctx, lines = make_ctx(tmp_path, runner=runner)
    ctx.state["render"] = _render_state()
    deploy.github_step(ctx, DBS)
    secrets = {c["args"][3]: c["input"] for c in runner.calls if c["args"][:3] == ["gh", "secret", "set"]}
    assert secrets == {"DATABASE_URL": DBS["live"]["pooled"], "DATABASE_URL_DIRECT": DBS["live"]["direct"],
                       "RADAR_GITHUB_TOKEN": TOKENS["RADAR_GITHUB_TOKEN"],
                       "ANTHROPIC_API_KEY": TOKENS["PROD_ANTHROPIC_API_KEY"]}
    for c in runner.calls:  # secret values never appear in argv or logs
        assert not any(v in " ".join(c["args"]) for v in secrets.values())
        assert "--repo" in c["args"] and "armaansingla14/Anti-Radar" in c["args"]
    assert ["gh", "variable", "set", "KEEPWARM_URL", "--body", "https://radar-live.onrender.com"] == \
        runner.calls[0]["args"][:6]
    assert ["gh", "variable", "set", "RADAR_LIVE", "--body", "true"] == runner.calls[-2]["args"][:6]
    assert runner.calls[-1]["args"][:5] == ["gh", "workflow", "run", "radar-ingest.yml", "-f"]
    assert not any(TOKENS["RADAR_GITHUB_TOKEN"] in line or "pw-live-secret" in line for line in lines)

    runner.calls.clear()
    deploy.github_step(ctx, DBS)
    assert not any(c["args"][1] == "workflow" for c in runner.calls)


def test_github_skips_missing_optional_anthropic_key(tmp_path):
    runner = FakeRunner()
    ctx, _ = make_ctx(tmp_path, runner=runner, PROD_ANTHROPIC_API_KEY="")
    ctx.state["render"] = _render_state()
    deploy.github_step(ctx, DBS)
    assert "ANTHROPIC_API_KEY" not in {c["args"][3] for c in runner.calls if c["args"][1] == "secret"}


def test_github_demo_only_sets_keepwarm_and_leaves_ingestion_off(tmp_path):
    runner = FakeRunner()
    ctx, _ = make_ctx(tmp_path, runner=runner, live=False)
    ctx.state["render"] = {"demo": _render_state()["demo"]}
    deploy.github_step(ctx, None)
    assert [c["args"][:6] for c in runner.calls] == [
        ["gh", "variable", "set", "KEEPWARM_URL", "--body", "https://radar-demo.onrender.com"]]


# ── Seed ──────────────────────────────────────────────────────────────────────

def test_seed_replays_locally_then_restores_into_neon_via_stdin(tmp_path):
    runner = FakeRunner(stdout="CREATE TABLE x ();\n")
    ctx, lines = make_ctx(tmp_path, runner=runner, live=False)
    deploy.seed_step(ctx, DBS)
    cmds = [c["args"] for c in runner.calls]
    assert cmds[1] == ["uv", "run", "alembic", "upgrade", "head"]
    assert cmds[2] == ["uv", "run", "radar", "seed-demo", "--reset", "--no-fictional"]
    for c in runner.calls[1:3]:  # offline, deterministic replay into the LOCAL database
        assert c["env"]["DATABASE_URL"] == deploy.LOCAL_DB_URL and c["env"]["LLM_PROVIDER"] == "none"
        assert c["env"]["DEMO_SNAPSHOT"] == "true" and c["env"]["ANTHROPIC_API_KEY"] == ""
        assert c["cwd"].endswith("backend")
    assert cmds[3][5] == "pg_dump" and "--no-owner" in cmds[3]
    restore = runner.calls[4]
    assert restore["args"][5] == "psql" and restore["input"] == "CREATE TABLE x ();\n"
    assert restore["args"][6] == DBS["demo"]["direct"]  # direct (non-pooled) host, libpq scheme
    assert not any("pw-demo-secret" in line for line in lines)
    assert deploy.libpq_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"


# ── Verify + docs ─────────────────────────────────────────────────────────────

def test_verify_checks_four_urls_and_writes_doc_and_state(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEPLOYMENT.md").write_text(
        f"# Title\n\n## Production URL\n\n{deploy.DOC_START}\nplaceholder\n{deploy.DOC_END}\n\n## Next\nkeep\n")
    ctx, _ = make_ctx(tmp_path)
    ctx.state.update(render=_render_state(), frontend_url="https://anti-radar.vercel.app")
    with respx.mock(assert_all_called=True) as m:
        live = m.get("https://radar-live.onrender.com/api/ready").mock(
            side_effect=[httpx.Response(503), httpx.Response(200)])
        m.get("https://radar-demo.onrender.com/api/ready").respond(200)
        m.get("https://anti-radar.vercel.app/api/proxy/health").respond(200)
        m.get("https://anti-radar.vercel.app/demo").respond(200)
        assert deploy.verify_step(ctx) is True
    assert live.call_count == 2
    doc = (tmp_path / "docs" / "DEPLOYMENT.md").read_text()
    assert "placeholder" not in doc and "https://anti-radar.vercel.app/demo" in doc
    assert "https://radar-live.onrender.com" in doc and doc.endswith("## Next\nkeep\n")
    assert json.loads((tmp_path / "deploy" / "state.json").read_text())["verified_at"]


def test_verify_reports_failure(tmp_path):
    ctx, lines = make_ctx(tmp_path)
    ctx.state.update(render=_render_state(), frontend_url="https://anti-radar.vercel.app")
    with respx.mock() as m:
        m.get(host="radar-live.onrender.com").respond(503)
        m.get().respond(200)
        assert deploy.verify_step(ctx, attempts=2) is False
    assert any("FAIL radar-live /api/ready: 503" in line for line in lines)


def test_update_doc_inserts_section_after_h1_when_missing():
    out = deploy.update_doc("# Deploy\n\nbody\n", "URLS")
    assert out.startswith(f"# Deploy\n\n## Production URL\n\n{deploy.DOC_START}\nURLS\n{deploy.DOC_END}\n")
    assert deploy.update_doc(out, "NEW").count("## Production URL") == 1


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_walks_every_step_without_network_or_writes(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    ctx, lines = make_ctx(tmp_path, runner=FakeRunner())
    ctx.dry_run, ctx.transport = True, deploy.dry_run_transport()
    with respx.mock() as m:  # no routes: any request on a real transport would fail the test
        assert deploy.run_steps(ctx, list(deploy.STEPS)) is True
        assert not m.calls
    assert not ctx.runner.calls  # gh / npx were only printed
    assert not (tmp_path / "deploy").exists() and not (tmp_path / ".env").exists()
    out = "\n".join(lines)
    for step in ("Neon", "Seed", "Render", "Vercel", "GitHub", "Verify"):
        assert f"== {step}" in out
    assert '"plan": "free"' in out and "vercel@latest deploy --prod" in out and "gh workflow run" in out
    for value in TOKENS.values():
        assert value not in out


def test_dry_run_demo_profile_skips_live_resources(tmp_path):
    (tmp_path / "docs").mkdir()
    ctx, lines = make_ctx(tmp_path, runner=FakeRunner(), live=False,
                          RADAR_GITHUB_TOKEN="", PROD_ANTHROPIC_API_KEY="")
    ctx.dry_run, ctx.transport = True, deploy.dry_run_transport()
    assert deploy.run_steps(ctx, list(deploy.STEPS)) is True
    out = "\n".join(lines)
    assert "radar-demo" in out and "radar_demo" in out
    assert "radar-live" not in out and "radar_live" not in out and "gh secret" not in out
    assert "gh workflow run" not in out and "KEEPWARM_URL" in out and "pg_dump" in out


def test_main_dry_run_and_check_exit_codes(monkeypatch, capsys):
    with respx.mock():
        assert deploy.main(["--dry-run", "--step", "neon"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    monkeypatch.setattr(deploy, "load_env", lambda _p: {})
    for name in TOKENS:
        monkeypatch.delenv(name, raising=False)
    assert deploy.main(["--check"]) == 1
    assert "MISSING  NEON_API_KEY" in capsys.readouterr().out


def test_broad_github_token_is_never_used_as_deploy_token():
    """A local GITHUB_TOKEN (often a broad `gh auth token`) must not stand in for the read-only deploy token."""
    env = {"GITHUB_TOKEN": "gho_broadscope0123456789"}
    assert deploy.token_value(env, "RADAR_GITHUB_TOKEN") == ""
