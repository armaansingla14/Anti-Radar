"""`radar portfolio …` commands (registered in radar/cli.py).

    radar portfolio sync [--config PATH]         # positions + comps from config/anti_fund_portfolio.yaml
    radar portfolio run [--day YYYY-MM-DD]       # the monitoring pass: events, impacts, questions, metrics, reports
    radar portfolio show [COMPANY]               # Needs Attention / one company's events
    radar portfolio brief [--day YYYY-MM-DD]     # print the stored Portfolio Brief (markdown)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

portfolio_app = typer.Typer(help="Portfolio Pulse: material changes in the public portfolio.", no_args_is_help=True)


def _console():
    from rich.console import Console

    return Console()


def _day(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise typer.BadParameter("day must be YYYY-MM-DD") from None


@portfolio_app.command("sync")
def sync(config: Annotated[Path | None, typer.Option("--config", help="portfolio config path")] = None) -> None:
    """Validate the public portfolio config and sync positions and comps onto Company entities."""
    from radar.db import session_scope
    from radar.portfolio.config import PortfolioConfigError, load_config
    from radar.portfolio.sync import sync_portfolio

    try:
        cfg = load_config(config)
    except PortfolioConfigError as e:
        _console().print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    with session_scope() as s:
        out = sync_portfolio(s, cfg)
    _console().print_json(json.dumps(out, default=str))


@portfolio_app.command("run")
def run(day: Annotated[str | None, typer.Option(help="YYYY-MM-DD (default: the data clock's today)")] = None,
        reports: bool = True) -> None:
    """Run the monitoring pass for a day (deterministic, offline)."""
    from radar.db import session_scope
    from radar.portfolio.run import run_portfolio

    with session_scope() as s:
        out = run_portfolio(s, _day(day), reports=reports)
    _console().print_json(json.dumps(out, default=str))


@portfolio_app.command("show")
def show(company: Annotated[str | None, typer.Argument(help="portfolio company slug")] = None) -> None:
    """Needs Attention (no argument) or one portfolio company's events in the window."""
    from rich.table import Table

    from radar.db import session_scope
    from radar.portfolio import views

    con = _console()
    with session_scope() as s:
        if company is None:
            day = views.data_day(s)
            f = views.funnel(s)
            con.print(f"[bold]Portfolio Pulse[/bold] {day.isoformat()}  ·  "
                      + (f"{f['raw_changes']} raw → {f['candidates']} candidates → {f['after_dedup']} events → "
                         f"{f['material']} material / {f['watch']} watch / {f['noise']} noise"
                         if f.get("available") else f["reason"]))
            for c in views.needs_attention(s, day):
                con.print(f"\n[bold]{c['company']['name']}[/bold]  {c['priority']}  score {c['score']:.0f}  "
                          f"→ {c['suggested_action']}")
                con.print(f"  {c['why_surfaced']}")
                for w in c["what_changed"]:
                    con.print(f"  [{w['materiality']}] {w['relation']:<10} {w['title'][:100]}")
                for q in c["questions"]:
                    con.print(f"  ? {q['text']}")
            return
        found = views.resolve_portfolio_company(s, company)
        if found is None:
            raise typer.BadParameter(f"no portfolio company {company!r}")
        v = views.company_view(s, *found)
        t = Table(title=f"{v['company']['name']} · events", title_justify="left")
        for col in ("Date", "Level", "Rule", "Relation", "Type", "Title"):
            t.add_column(col)
        for e in v["events"] + v["competitor_events"]:
            t.add_row(e["event_at"][:10], e["materiality"], e["materiality_rule"], e["relation"] or "",
                      f"{e['event_type']}.{e['event_subtype']}", e["title"][:80])
        con.print(t)
        con.print(f"  noise filtered: {v['noise_count']}  ·  open questions: "
                  f"{sum(1 for q in v['questions'] if q['status'] == 'OPEN')}")


@portfolio_app.command("brief")
def brief(day: Annotated[str | None, typer.Option(help="YYYY-MM-DD (default: latest)")] = None) -> None:
    """Print the stored Portfolio Brief."""
    from radar.db import session_scope
    from radar.portfolio import views

    with session_scope() as s:
        r = views.brief_for(s, day)
        if r is None:
            _console().print("[dim]no portfolio brief yet (radar portfolio run)[/dim]")
            raise typer.Exit(code=1)
        print(r.markdown)
