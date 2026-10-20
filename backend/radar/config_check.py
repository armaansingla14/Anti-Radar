"""Startup configuration validation.

``validate(settings)`` runs at API startup (lifespan), in ``backend/start.sh`` (``python -m radar.config_check``)
and should run at CLI start. Hosted environments (``APP_ENV=production|demo``) must not silently fall back to
localhost defaults; production additionally requires an admin token so writes are not public.

Optional credentials (LLM, GitHub, X) never fail validation — the matching providers are reported as
DEGRADED/DISABLED with a reason (see ``radar.providers.registry``).

All problems are collected and raised together as one ``ConfigError`` with a readable message.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from radar.settings import DEFAULT_DATABASE_URL, Settings, get_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
MIN_ADMIN_TOKEN_LEN = 16
Role = Literal["api", "cli"]


class ConfigError(RuntimeError):
    def __init__(self, problems: list[str], app_env: str = "production") -> None:
        self.problems = problems
        self.app_env = app_env
        super().__init__(self.render())

    def render(self) -> str:
        lines = [f"ANTI RADAR configuration invalid for APP_ENV={self.app_env} ({len(self.problems)} problem"
                 f"{'s' if len(self.problems) != 1 else ''}):"]
        lines += [f"  - {p}" for p in self.problems]
        lines.append("See .env.example and docs/DEPLOYMENT.md.")
        return "\n".join(lines)


@dataclass
class ConfigReport:
    app_env: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    providers: dict[str, str] = field(default_factory=dict)  # key -> "state: reason"

    @property
    def ok(self) -> bool:
        return not self.problems


def _host(url: str) -> str | None:
    try:
        return (make_url(url).host or "").lower()
    except Exception:  # noqa: BLE001
        return None


def _is_local(host: str | None) -> bool:
    return host is None or host in LOCAL_HOSTS or host.startswith("127.")


def check(settings: Settings | None = None, *, role: Role = "api") -> ConfigReport:
    """Collect problems (fatal in hosted envs) and warnings without raising.

    ``role="cli"`` is for one-shot jobs (GitHub Actions): they serve no browser and accept no writes over HTTP,
    so FRONTEND_URL and ADMIN_TOKEN are not required.
    """
    from radar.providers import registry

    s = settings or get_settings()
    rep = ConfigReport(app_env=s.app_env)
    hosted = s.is_deployed
    # Local production-parity runs may opt out of the "not localhost / https" checks.
    strict = hosted and not s.allow_local_production
    fatal = rep.problems.append if strict else rep.warnings.append

    # Database
    if _host(s.database_url) is None:
        rep.problems.append("DATABASE_URL is not a valid database URL")
    elif hosted and s.database_url == DEFAULT_DATABASE_URL:
        fatal("DATABASE_URL is not set (still the localhost development default)")
    elif hosted and _is_local(_host(s.database_url)):
        fatal(f"DATABASE_URL points at a local host ({_host(s.database_url) or 'unix socket'})")
    if s.database_url_direct:
        if _host(s.database_url_direct) is None:
            rep.problems.append("DATABASE_URL_DIRECT is not a valid database URL")
        elif hosted and _is_local(_host(s.database_url_direct)):
            fatal("DATABASE_URL_DIRECT points at a local host")
    elif hosted and "-pooler" in (_host(s.database_url) or ""):
        rep.warnings.append("DATABASE_URL is a pooled (-pooler) host and DATABASE_URL_DIRECT is unset; "
                            "migrations will run through the pooler")

    # Frontend origin (CORS)
    if role == "cli":
        pass
    elif s.frontend_url:
        u = urlparse(s.frontend_url)
        if u.scheme not in ("http", "https") or not u.netloc:
            rep.problems.append(f"FRONTEND_URL is not a URL: {s.frontend_url!r}")
        elif hosted and u.scheme != "https":
            fatal("FRONTEND_URL must use https")
        elif hosted and _is_local(u.hostname):
            fatal("FRONTEND_URL points at localhost")
    elif hosted:
        fatal("FRONTEND_URL is not set (needed for CORS)")

    # Writes
    if role == "cli":
        pass
    elif s.app_env == "production":
        if not s.admin_token:
            rep.problems.append("ADMIN_TOKEN is not set — writes (ingest, memo, feedback, watchlist) would be public")
        elif len(s.admin_token) < MIN_ADMIN_TOKEN_LEN:
            rep.problems.append(f"ADMIN_TOKEN is too short (minimum {MIN_ADMIN_TOKEN_LEN} characters)")
    elif s.app_env == "demo" and not s.admin_token:
        rep.warnings.append("ADMIN_TOKEN not set: the demo backend accepts writes (ingest stays blocked in "
                            "snapshot mode)")

    # Thesis config (baked into the image at /app/config)
    if not s.thesis_path.exists():
        (rep.problems if hosted else rep.warnings).append(f"THESIS_PATH does not exist: {s.thesis_path}")

    # Snapshot / synthetic
    if s.app_env == "production" and s.demo_snapshot:
        rep.warnings.append("DEMO_SNAPSHOT=true in production pins the clock to the snapshot date")
    if s.app_env == "production" and s.demo_synthetic:
        rep.warnings.append("DEMO_SYNTHETIC=true in production mixes the fictional dataset into feeds")
    if s.today_override and hosted:
        rep.warnings.append(f"TODAY_OVERRIDE={s.today_override} pins the data clock")

    # Optional providers: never fatal, reported with reasons.
    for key, av in registry.all_availability(s).items():
        rep.providers[key] = av.state + (f": {av.reason}" if av.reason else "")
        if av.state != "live":
            rep.warnings.append(f"provider {key} {av.state.upper()}: {av.reason}")
    llm = registry.llm_availability(s)
    rep.providers["llm"] = llm.state + (f": {llm.reason}" if llm.reason else "")
    if llm.state != "live":
        rep.warnings.append(f"LLM {llm.state.upper()}: {llm.reason}")
    return rep


def validate(settings: Settings | None = None, *, role: Role = "api") -> ConfigReport:
    """Raise ``ConfigError`` listing every problem; return the report (with warnings) otherwise."""
    rep = check(settings, role=role)
    if rep.problems:
        raise ConfigError(rep.problems, rep.app_env)
    return rep


def validate_or_exit(settings: Settings | None = None, *, role: Role = "cli", quiet: bool = False) -> ConfigReport:
    """CLI entry: print a clean error (no traceback) and exit 2 on invalid configuration."""
    try:
        rep = validate(settings, role=role)
    except ConfigError as e:
        print(e.render(), file=sys.stderr)
        raise SystemExit(2) from None
    if not quiet:
        for w in rep.warnings:
            print(f"config warning: {w}", file=sys.stderr)
    return rep


if __name__ == "__main__":  # python -m radar.config_check [--cli]
    validate_or_exit(role="cli" if "--cli" in sys.argv[1:] else "api")
    print(f"config ok (APP_ENV={get_settings().app_env})")
