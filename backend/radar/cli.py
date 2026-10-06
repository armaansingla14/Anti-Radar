"""`radar` — operate ANTI RADAR from the terminal.

    radar ingest github [--query Q] [--repo owner/name] [--org ORG]
    radar ingest arxiv  [--category cs.RO] [--query Q]
    radar ingest hn
    radar ingest url https://acme.ai
    radar ingest x https://x.com/someone/status/123 [--text "..."]
    radar ingest all
    radar score                      # classify → stage → score
    radar brief                      # print today's morning brief
    radar investigate "Halcyon"      # RADAR ANALYST → Anti Brief
    radar memo "Halcyon"
    radar status
    radar seed-demo [--reset] [--if-empty]
    radar demo capture               # re-capture the real-data snapshot
    radar worker                     # scheduled ingestion loop
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=True, help="ANTI RADAR — Conviction Before Consensus.")
ingest_app = typer.Typer(no_args_is_help=True, help="Pull signals from a source.")
demo_app = typer.Typer(no_args_is_help=True, help="Demo data tooling.")
app.add_typer(ingest_app, name="ingest")
app.add_typer(demo_app, name="demo")
console = Console()

ACCENT = "#9b2a30"


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, markup=False)],
    )
    from radar import clock

    clock.init_from_db()


def _banner(title: str) -> None:
    console.print(f"[bold]ANTI RADAR[/bold] [dim]·[/dim] [{ACCENT}]{title}[/]")


def _report(result) -> None:
    run = result[0]
    color = {"ok": "green", "partial": "yellow"}.get(run.status, "red")
    console.print(
        f"  [{color}]{run.status.upper()}[/] {run.provider}: {run.records_fetched} fetched · "
        f"{run.records_accepted} accepted · {run.records_rejected} rejected · {run.signals_created} new signals"
        + (f" · {run.request_count} requests" if run.request_count else "")
    )
    for e in (run.errors or [])[:5]:
        console.print(f"    [dim]! {e}[/dim]")


# ── ingest ───────────────────────────────────────────────────────────────────


@ingest_app.command("github")
def ingest_github(
    query: Annotated[list[str] | None, typer.Option("--query", "-q", help="Discovery query (repeatable)")] = None,
    repo: Annotated[str | None, typer.Option(help="owner/name")] = None,
    org: Annotated[str | None, typer.Option(help="GitHub organization")] = None,
    per_query: int = 4,
    max_repos: int = 60,
) -> None:
    """Discover accelerating repositories (thesis queries by default)."""
    from radar import ops

    _banner("GitHub")
    _report(ops.ingest_github(queries=query, repo=repo, org=org, per_query=per_query, max_repos=max_repos))


@ingest_app.command("arxiv")
def ingest_arxiv(
    category: Annotated[list[str] | None, typer.Option("--category", "-c")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    max_results: Annotated[int | None, typer.Option()] = None,
    days: int = 21,
) -> None:
    """New papers in thesis categories (or a keyword search)."""
    from radar import ops

    _banner("arXiv")
    _report(ops.ingest_arxiv(categories=category, max_results=max_results, days=days, query=query))


@ingest_app.command("hn")
def ingest_hn(days: int = 14, topics: bool = True) -> None:
    """Show HN / Launch HN + thesis-topic discussions."""
    from radar import ops

    _banner("Hacker News")
    _report(ops.ingest_hn(days=days, topics=topics))


@ingest_app.command("url")
def ingest_url(url: str, name: Annotated[str | None, typer.Option(help="Company name")] = None) -> None:
    """Crawl a company website (robots.txt respected) incl. public job boards."""
    from radar import ops

    _banner("Website")
    _report(ops.ingest_url(url, company_name=name))


@ingest_app.command("x")
def ingest_x(url: str, text: Annotated[str | None, typer.Option(help="Pasted post text if oEmbed fails")] = None) -> None:
    """Ingest a single X post URL (official oEmbed; never fabricated)."""
    from radar import ops

    _banner("X (manual)")
    run, stats, info = ops.ingest_x_manual(url, text)
    _report((run, stats))
    if info:
        console.print(f"  [dim]{json.dumps(info, default=str)[:300]}[/dim]")


@ingest_app.command("all")
def ingest_all(websites: bool = True) -> None:
    """Full sweep: HN → arXiv → GitHub → websites, then recompute scores."""
    from radar import ops

    _banner("Full sweep")
    _report(ops.ingest_hn())
    _report(ops.ingest_arxiv())
    _report(ops.ingest_github())
    if websites:
        for r in ops.crawl_company_sites(limit=15):
            _report(r)
    _score()


# ── derive ───────────────────────────────────────────────────────────────────


def _score() -> None:
    from radar import ops

    out = ops.recompute()
    s = out["scored"]
    console.print(
        f"  Scored {s['companies_scored']} companies · {s['people_scored']} people "
        f"(cohort {s['company_cohort']}) · {s['attention_flagged']} attention-gap flags · version {s['version']}"
    )


@app.command()
def score() -> None:
    """Classify thesis topics, estimate stages, compute ANTI SCOREs."""
    _banner("Scoring")
    _score()


@app.command()
def brief(markdown: bool = False) -> None:
    """Print the ANTI RADAR Morning Brief."""
    from radar.db import session_scope
    from radar.services.brief import morning_brief

    with session_scope() as s:
        b = morning_brief(s)
    if markdown:
        print(b.markdown)
        return
    from rich.markdown import Markdown

    console.print(Markdown(b.markdown))


def _find_entity(name: str):
    from sqlalchemy import func, or_, select

    from radar.db import session_scope
    from radar.models import Company, Person

    with session_scope() as s:
        for model, kind in ((Company, "company"), (Person, "person")):
            row = s.scalar(
                select(model)
                .where(or_(model.slug == name.lower(), func.lower(model.name).contains(name.lower())))
                .order_by(model.is_demo)
                .limit(1)
            )
            if row:
                return kind, row.id, row.name
    raise typer.BadParameter(f"No company or person matching {name!r}")


@app.command()
def investigate(name: str, live: bool = True) -> None:
    """Run RADAR ANALYST on a company or founder and print the Anti Brief."""
    from rich.markdown import Markdown

    from radar.agent.analyst import investigate as run_investigation
    from radar.db import session_scope

    kind, eid, label = _find_entity(name)
    _banner(f"Investigating {label}")

    def on_event(ev: dict) -> None:
        t = ev.get("type")
        if t in ("step", "tool_call", "tool_result"):
            msg = ev.get("label") or ev.get("message") or ev.get("summary") or ev.get("tool") or ""
            console.print(f"  [dim]{t:<11}[/dim] {str(msg)[:110]}")

    with session_scope() as s:
        report = run_investigation(s, kind, eid, live_fetch=live, on_event=on_event)
        md = report.markdown
        console.print(f"\n  generator={report.generator} model={report.model} cost=${report.cost_usd:.3f}\n")
    console.print(Markdown(md))


@app.command()
def memo(name: str) -> None:
    """Generate an Anti Memo (Markdown) for a company or founder."""
    from radar.agent.memo import generate_memo
    from radar.db import session_scope

    kind, eid, label = _find_entity(name)
    with session_scope() as s:
        report = generate_memo(s, kind, eid)
        print(report.markdown)


@app.command()
def status() -> None:
    """Provider health, data volumes and LLM spend."""
    from radar.db import session_scope
    from radar.services.status import system_status

    with session_scope() as s:
        st = system_status(s)
    t = Table(title="ANTI RADAR · Sources", title_justify="left", show_lines=False)
    for col in ("Source", "State", "Last success", "Collected", "Rejected", "Signals", "p50 ms", "Note"):
        t.add_column(col)
    for p in st.providers:
        color = {"healthy": "green", "degraded": "yellow", "disabled": "dim", "never_run": "dim"}.get(p.state, "red")
        t.add_row(
            p.label, f"[{color}]{p.state}[/]", _ago(p.last_success_at), str(p.records_collected),
            str(p.records_rejected), str(p.signals_created), f"{p.latency_ms_p50:.0f}" if p.latency_ms_p50 else "–",
            (p.reason or "")[:48],
        )
    console.print(t)
    console.print(f"LLM: {json.dumps(st.llm, default=str)}")
    console.print(f"DB: {json.dumps(st.database, default=str)}")


def _ago(dt: datetime | None) -> str:
    if not dt:
        return "–"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit} ago"
    return "just now"


# ── demo ─────────────────────────────────────────────────────────────────────


@app.command("seed-demo")
def seed_demo(
    reset: bool = False,
    if_empty: Annotated[bool, typer.Option("--if-empty")] = False,
    snapshot: bool = True,
    fictional: bool = True,
) -> None:
    """Load the committed real-data snapshot + labeled fictional set, then score."""
    from radar.demo.seed import seed_demo as run

    _banner("Seeding demo")
    out = run(reset=reset, if_empty=if_empty, snapshot=snapshot, fictional=fictional)
    console.print_json(json.dumps(out, default=str))


@demo_app.command("capture")
def demo_capture(
    max_repos: int = 70,
    per_query: int = 3,
    papers_per_category: int = 30,
    websites: int = 20,
) -> None:
    """Capture a fresh real-data snapshot from live public APIs (GitHub, arXiv, HN, websites)."""
    from radar import clock, ops
    from radar.demo.seed import SNAPSHOT_DIR
    from radar.retro import RETRO_REPOS

    tmp = SNAPSHOT_DIR.with_name("snapshot.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    _banner("Capturing real-data snapshot")
    with ops.capturing(tmp):
        _report(ops.ingest_hn(days=21, topic_days=60))
        _report(ops.ingest_arxiv(max_results=papers_per_category, days=21))
        _report(ops.ingest_github(per_query=per_query, max_repos=max_repos))
        for full in RETRO_REPOS:
            _report(ops.ingest_github(repo=full))
        ops.recompute()
        for r in ops.crawl_company_sites(limit=websites):
            _report(r)
    (tmp / "MANIFEST.json").write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "as_of": clock.today().isoformat(),
                "sources": ["github", "arxiv", "hackernews", "website"],
                "note": "Real public data captured from official APIs. Facts are true as of the capture date.",
            },
            indent=2,
        )
    )
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)
    tmp.rename(SNAPSHOT_DIR)
    size = sum(p.stat().st_size for p in SNAPSHOT_DIR.glob("*")) / 1e6
    console.print(f"  Snapshot written to {SNAPSHOT_DIR} ({size:.1f} MB)")


@demo_app.command("export-llm-cache")
def export_llm_cache(path: Path | None = None) -> None:
    """Export cached LLM outputs so the demo works without an API key."""
    from radar.demo.seed import LLM_CACHE_FILE
    from radar.llm.cache import export_cache

    n = export_cache(path or LLM_CACHE_FILE)
    console.print(f"  Exported {n} cached LLM responses")


# ── runtime ──────────────────────────────────────────────────────────────────


@app.command()
def worker() -> None:
    """Scheduled ingestion: HN hourly, GitHub every 6h, arXiv daily, websites daily, scoring after each."""
    from radar.worker import run_forever

    _banner("Worker")
    run_forever()


if __name__ == "__main__":
    app()
