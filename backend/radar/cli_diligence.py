"""`radar diligence …` commands (registered in radar/cli.py).

    radar diligence run "Acme" [--capture/--no-capture] [--budget 180]   # live collection + analysis
    radar diligence run acme --from-event <event id> [--seed-url URL …]  # portfolio handoff: seed the event's pages
    radar diligence replay acme [--all]                                   # offline, from radar/demo/diligence/
    radar diligence show "Acme"                                           # print the claim ledger
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

diligence_app = typer.Typer(help="Technical diligence: claim ledger, evidence, underwriting.", no_args_is_help=True)

STATUS_COLORS = {"VERIFIED": "green", "SUPPORTED": "green", "PARTIALLY_SUPPORTED": "yellow", "CONTRADICTED": "red",
                 "UNVERIFIED": "dim", "UNTESTABLE": "dim"}


def _console():
    from rich.console import Console

    return Console()


def _print_run(run) -> None:
    con = _console()
    color = "green" if run.status == "complete" else "red"
    s = run.summary or {}
    con.print(f"  [{color}]{run.status.upper()}[/] mode={run.mode} method={run.method_version} "
              f"sources={run.sources_analyzed} claims={s.get('claims', 0)} coverage={run.evidence_coverage}")
    if run.error:
        con.print(f"  [red]{run.error}[/red]")
    for k, v in (s.get("by_status") or {}).items():
        if v:
            con.print(f"    {k:<20} {v}")


@diligence_app.command("run")
def run_cmd(
    company: str,
    capture: Annotated[bool, typer.Option("--capture/--no-capture", help="Write radar/demo/diligence/<slug>.json.gz")] = True,
    budget: Annotated[int, typer.Option(min=10, max=900, help="Collection time budget (seconds)")] = 180,
    seed_url: Annotated[list[str] | None, typer.Option(
        "--seed-url", help="Also collect this company page (own domain only; repeatable)")] = None,
    from_event: Annotated[str | None, typer.Option(
        "--from-event", help="Portfolio MaterialEvent id: collect its source URLs and log the handoff")] = None,
) -> None:
    """Collect public artifacts for a company (live network) and analyse them."""
    from radar.db import session_scope
    from radar.diligence.run import DiligenceError, run_diligence

    try:
        with session_scope() as s:
            run = run_diligence(s, company, live=True, budget_s=budget, capture=capture, seed_urls=seed_url or None,
                                event_id=from_event)
            _print_run(run)
    except DiligenceError as e:
        raise typer.BadParameter(str(e)) from None


@diligence_app.command("replay")
def replay_cmd(
    slug: Annotated[str | None, typer.Argument(help="Company slug (file radar/demo/diligence/<slug>.json.gz)")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Replay every captured run")] = False,
    path: Annotated[Path | None, typer.Option(help="Capture file (default: the demo capture for <slug>)")] = None,
) -> None:
    """Re-run analysis offline from captured artifacts (no network)."""
    from radar.db import session_scope
    from radar.diligence.capture import CaptureError, default_path, replay, replay_all

    con = _console()
    if all_:
        with session_scope() as s:
            for r in replay_all(s):
                con.print(f"  {r['file']}: {r['status']}" + (f" ({r.get('error')})" if r.get("error") else ""))
        return
    if not slug and not path:
        raise typer.BadParameter("give a company slug, --path, or --all")
    try:
        with session_scope() as s:
            run = replay(s, path or default_path(slug or ""))
            _print_run(run)
    except CaptureError as e:
        raise typer.BadParameter(str(e)) from None


@diligence_app.command("show")
def show_cmd(company: str) -> None:
    """Print the latest claim ledger for a company."""
    from rich.table import Table
    from sqlalchemy import select

    from radar.db import session_scope
    from radar.diligence.run import diligence_status, latest_run, resolve_company
    from radar.diligence.taxonomy import STATUS_LABELS
    from radar.models import DiligenceClaim

    con = _console()
    with session_scope() as s:
        c = resolve_company(s, company)
        if c is None:
            raise typer.BadParameter(f"no company matches {company!r}")
        st = diligence_status(s, c.id)
        run = latest_run(s, c.id, complete_only=True)
        con.print(f"[bold]{c.name}[/bold] · diligence {st['status']}")
        if run is None:
            return
        t = Table(show_lines=False)
        for col in ("#", "Type", "Status", "Testability", "Conf.", "Claim"):
            t.add_column(col)
        for cl in s.scalars(select(DiligenceClaim).where(DiligenceClaim.run_id == run.id)
                            .order_by(DiligenceClaim.ordinal)):
            color = STATUS_COLORS.get(cl.verification_status, "white")
            t.add_row(str(cl.ordinal), cl.claim_type, f"[{color}]{STATUS_LABELS[cl.verification_status]}[/]",
                      cl.testability, cl.confidence, cl.claim_text[:100])
        con.print(t)
        for p in st.get("pending_review") or []:
            con.print(f"  [yellow]needs review[/] {p['label'][:110]}")
