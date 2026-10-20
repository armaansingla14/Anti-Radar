"""Settings contract, startup validation, provider registry and engine options (no database)."""

from __future__ import annotations

import pytest

from radar import config_check
from radar.providers import registry
from radar.settings import Settings

PROD_OK = dict(
    app_env="production",
    database_url="postgresql://u:p@ep-cool-1234-pooler.us-east-2.aws.neon.tech/radar_live?sslmode=require",
    database_url_direct="postgresql://u:p@ep-cool-1234.us-east-2.aws.neon.tech/radar_live?sslmode=require",
    frontend_url="https://radar.example.com",
    admin_token="x" * 32,
)


def make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("APP_ENV", "DEMO_MODE", "DEMO_SYNTHETIC", "DEMO_SNAPSHOT", "ADMIN_TOKEN", "FRONTEND_URL",
              "DATABASE_URL", "DATABASE_URL_DIRECT", "GITHUB_TOKEN", "RADAR_GITHUB_TOKEN", "X_BEARER_TOKEN",
              "DB_POOL_SIZE", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "DB_MAX_OVERFLOW", "RADAR_NULLPOOL", "ALLOW_LOCAL_PRODUCTION"):
        monkeypatch.delenv(k, raising=False)


# ── settings contract ────────────────────────────────────────────────────────


def test_defaults():
    s = make()
    assert s.app_env == "development"
    assert s.demo_synthetic is False and s.demo_mode is False
    assert s.demo_snapshot is False and s.admin_token is None
    assert (s.db_pool_size, s.db_max_overflow) == (10, 10)


def test_env_aliases(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RADAR_GITHUB_TOKEN", "ghp_radar")
    monkeypatch.setenv("ADMIN_TOKEN", "")  # blank in .env means unset
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_SNAPSHOT", "1")
    s = make()
    assert s.demo_synthetic is True and s.demo_mode is True
    assert s.github_token == "ghp_radar"
    assert s.admin_token is None
    assert s.app_env == "demo" and s.demo_snapshot is True


def test_demo_synthetic_wins_and_demo_mode_is_read_only(monkeypatch):
    monkeypatch.setenv("DEMO_SYNTHETIC", "false")
    s = make()
    assert s.demo_synthetic is False
    with pytest.raises((AttributeError, ValueError)):
        s.demo_mode = True  # type: ignore[misc]


def test_invalid_app_env_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValueError):
        make()


def test_postgres_urls_normalized_to_psycopg():
    s = make(**PROD_OK)
    assert s.database_url.startswith("postgresql+psycopg://")
    assert "pooler" not in s.migration_url and s.migration_url.startswith("postgresql+psycopg://")
    assert make(database_url="postgres://a:b@h/db").database_url == "postgresql+psycopg://a:b@h/db"


def test_cors_includes_frontend_url():
    s = make(cors_origins="http://localhost:3000", frontend_url="https://radar.example.com/")
    assert s.cors_origin_list() == ["http://localhost:3000", "https://radar.example.com"]


def test_no_gh_cli_fallback_outside_development(monkeypatch):
    import radar.settings as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _n: (_ for _ in ()).throw(AssertionError("gh probed")))
    assert make(app_env="production").resolved_github_token() is None


# ── config validation ────────────────────────────────────────────────────────


def test_production_ok():
    rep = config_check.validate(make(**PROD_OK))
    assert rep.ok
    # optional providers are reported, never fatal
    assert rep.providers["x"].startswith("disabled")
    assert rep.providers["github"].startswith("degraded")


def test_production_lists_every_problem():
    with pytest.raises(config_check.ConfigError) as ei:
        config_check.validate(make(app_env="production", frontend_url="http://radar.example.com"))
    msg = str(ei.value)
    assert "APP_ENV=production" in msg
    assert "DATABASE_URL is not set" in msg
    assert "FRONTEND_URL must use https" in msg
    assert "ADMIN_TOKEN is not set" in msg
    assert len(ei.value.problems) == 3


@pytest.mark.parametrize("url", ["postgresql+psycopg://u:p@localhost/radar", "postgresql+psycopg://u:p@127.0.0.1/r"])
def test_production_forbids_localhost_db(url):
    with pytest.raises(config_check.ConfigError, match="local host"):
        config_check.validate(make(**{**PROD_OK, "database_url": url}))


def test_production_requires_frontend_url_and_long_token():
    with pytest.raises(config_check.ConfigError) as ei:
        config_check.validate(make(**{**PROD_OK, "frontend_url": None, "admin_token": "short"}))
    assert any("FRONTEND_URL is not set" in p for p in ei.value.problems)
    assert any("too short" in p for p in ei.value.problems)


def test_demo_env_does_not_need_admin_token():
    rep = config_check.validate(make(**{**PROD_OK, "app_env": "demo", "admin_token": None, "demo_snapshot": True}))
    assert rep.ok
    assert any("ADMIN_TOKEN not set" in w for w in rep.warnings)


def test_development_is_lenient():
    rep = config_check.validate(make())
    assert rep.ok


def test_local_production_parity_downgrades_to_warnings():
    s = make(app_env="production", database_url="postgresql+psycopg://radar:radar@db:5432/radar",
             frontend_url="http://localhost:3001", admin_token="y" * 20, allow_local_production=True)
    rep = config_check.validate(s)
    assert rep.ok and any("https" in w for w in rep.warnings)


def test_validate_or_exit_prints_clean_error(capsys):
    with pytest.raises(SystemExit) as ei:
        config_check.validate_or_exit(make(app_env="production"))
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "configuration invalid" in err and "Traceback" not in err


# ── provider registry ────────────────────────────────────────────────────────


def test_registry_descriptors():
    keys = [d.key for d in registry.descriptors()]
    assert keys == ["github", "hackernews", "arxiv", "website", "jobs", "x"]
    assert "demo" not in keys and "snapshot" not in keys


def test_registry_availability():
    s = make()
    gh = registry.availability("github", s, github_authenticated=False)
    assert gh.state == "degraded" and "60 req/h" in gh.reason and gh.enabled
    assert registry.availability("github", make(github_token="t")).state == "live"
    x = registry.availability("x", s)
    assert x.state == "disabled" and x.reason.startswith("API access not configured") and not x.enabled
    assert registry.availability("x", make(x_bearer_token="b")).state == "live"
    assert registry.availability("arxiv", s).state == "live"


def test_llm_availability():
    assert registry.llm_availability(make(llm_provider="none")).state == "disabled"
    assert "ANTHROPIC_API_KEY" in registry.llm_availability(make(llm_provider="anthropic")).reason
    assert registry.llm_availability(make(llm_provider="anthropic", anthropic_api_key="k")).state == "live"


# ── engine options ───────────────────────────────────────────────────────────


def test_engine_kwargs(monkeypatch):
    from sqlalchemy import pool

    from radar import db

    url = "postgresql+psycopg://u:p@h/db"
    kw = db.engine_kwargs(url, nullpool=False)
    assert kw["connect_args"] == {"prepare_threshold": None}
    assert kw["pool_recycle"] == 300 and "pool_size" in kw
    monkeypatch.setenv("RADAR_NULLPOOL", "1")
    kw = db.engine_kwargs(url)
    assert kw["poolclass"] is pool.NullPool and "pool_size" not in kw


def test_cli_role_skips_web_only_requirements():
    s = make(app_env="production", database_url=PROD_OK["database_url"])
    assert config_check.validate(s, role="cli").ok
    with pytest.raises(config_check.ConfigError):
        config_check.validate(s, role="api")
    with pytest.raises(config_check.ConfigError, match="local host"):
        config_check.validate(make(app_env="production", database_url="postgresql+psycopg://u@localhost/x"),
                              role="cli")
