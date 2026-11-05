"""`radar eval …` (registered in radar/cli.py).

    radar eval                       # evaluate stored reports, promote HIGH flags, run every regression case
    radar eval --agent analyst       # one agent's reports and cases
    radar eval --case affiliation-001
    radar eval --seed-failures       # (re)load the documented failure ledger
    radar eval --compare-models      # LLM-capable cases on the template and every configured model
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

eval_app = typer.Typer(help="Agent evaluation: metrics, failures, regression cases, model comparison.",
                       invoke_without_command=True, add_completion=False)
console = Console()


def _fmt(m: dict | None, pct: bool = True) -> str:
    if not m or m.get("value") is None:
        return "—"
    v = m["value"]
    return f"{v * 100:.0f}% (n={m['n']})" if pct else f"{v:,.0f} (n={m['n']})"


def _agent_table(rows: list[dict]) -> Table:
    t = Table(title="Agents (delivered output; guard repairs are flags)", show_lines=False)
    for col in ("agent", "runs", "success", "citation validity", "unsupported", "fallback", "p50 ms",
                "review pending"):
        t.add_column(col)
    for r in rows:
        m = r["metrics"]
        t.add_row(r["name"], str(r["runs"]), _fmt(m["success_rate"]), _fmt(m["citation_validity"]),
                  _fmt(m["unsupported_claim_rate"]), _fmt(m["fallback_rate"]), _fmt(m["latency_p50_ms"], pct=False),
                  str(m["human_review_pending"]["value"]))
    return t


def _compare(json_out: bool) -> None:
    from radar.db import session_scope
    from radar.evals.compare import compare_models, configured_models, normalize, recommend_models
    from radar.models import EvalCaseResult

    models = configured_models()
    if not models:
        console.print("no models configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY)")
        return
    from sqlalchemy import select

    from radar.db import engine

    with engine().connect() as conn:
        trans = conn.begin()
        from sqlalchemy.orm import Session

        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            batch = compare_models(s, models=models)
            rows = list(s.scalars(select(EvalCaseResult).where(EvalCaseResult.batch_id == batch)))
            snapshot = [{c.name: getattr(r, c.name) for c in EvalCaseResult.__table__.columns} for r in rows]
        finally:
            s.close()
            trans.rollback()  # fixtures from the cases never persist
    with session_scope() as s:
        for d in snapshot:
            s.add(EvalCaseResult(**d))
        s.flush()
        norm = normalize(s.scalars(select(EvalCaseResult).where(EvalCaseResult.batch_id == batch)))
        recs = recommend_models(s)
    if json_out:
        console.print_json(json.dumps({"normalized": norm, "recommendations": recs}, default=str))
        return
    t = Table(title=f"Model comparison (batch {batch})")
    for col in ("task", "provider", "model", "n", "pass", "citation validity", "unsupported", "p50 ms", "$/case"):
        t.add_column(col)
    for r in norm:
        t.add_row(r["task"], r["provider"], r["model"], str(r["n"]), _fmt(r["pass_rate"]),
                  _fmt(r["citation_validity"]), _fmt(r["unsupported_claim_rate"]),
                  _fmt(r["latency_p50_ms"], pct=False),
                  "—" if r["cost_per_case"]["value"] is None else f"${r['cost_per_case']['value']:.4f}")
    console.print(t)
    for r in recs:
        rec = r["recommendation"]
        console.print(f"  {r['task']}: {(rec['provider'] + '/' + rec['model']) if rec else 'no recommendation'}"
                      f" — {r['reason']}")


@eval_app.callback()
def main(
    ctx: typer.Context,
    agent: str | None = typer.Option(None, "--agent", help="analyst | meeting | memo | morning | diligence | name"),
    case: str | None = typer.Option(None, "--case", help="run one regression case by id"),
    compare_models: bool = typer.Option(False, "--compare-models", help="compare configured models on the "
                                        "LLM-capable cases (costs money; only providers with a key)"),
    seed_failures: bool = typer.Option(False, "--seed-failures", help="(re)load the documented failure ledger"),
    promote: bool = typer.Option(True, "--promote/--no-promote", help="promote HIGH flags to OPEN failures"),
    json_out: bool = typer.Option(False, "--json", help="print JSON instead of tables"),
) -> None:
    """Evaluate stored agent outputs and run the regression cases (template baseline, no network)."""
    if ctx.invoked_subcommand is not None:
        return
    from sqlalchemy.orm import Session

    from radar.db import engine, session_scope
    from radar.evals.cases import CaseError, load_cases
    from radar.evals.failures import promote_flags, seed_documented_failures
    from radar.evals.metrics import agent_metrics, evaluate_all
    from radar.evals.runner import run_cases, store_outcomes
    from radar.evals.taxonomy import resolve_agent_alias

    agent_name = None
    if agent:
        agent_name = resolve_agent_alias(agent)
        if agent_name is None:
            console.print(f"[red]unknown agent {agent!r}[/red]")
            raise typer.Exit(2)
    try:
        all_cases = load_cases()
    except CaseError as e:
        console.print(f"[red]invalid case file: {e}[/red]")
        raise typer.Exit(2) from None
    if case:
        selected = [c for c in all_cases if c.id == case]
        if not selected:
            console.print(f"[red]unknown case {case!r}[/red] (known: {', '.join(c.id for c in all_cases)})")
            raise typer.Exit(2)
    else:
        selected = [c for c in all_cases if agent_name is None or c.agent == agent_name]

    if seed_failures:
        with session_scope() as s:
            counts = seed_documented_failures(s)
        console.print(f"  documented failures: {counts['created']} created, {counts['updated']} updated")
    if compare_models:
        _compare(json_out)
        return

    summary: dict = {}
    if not case:
        with session_scope() as s:
            evs = evaluate_all(s, agent=agent_name)
            promoted = promote_flags(s) if promote else {"created": 0, "skipped": 0}
            summary["evaluated"] = len(evs)
            summary["promoted"] = promoted
            rows = agent_metrics(s)
        rows = [r for r in rows if agent_name is None or r["name"] == agent_name]
        summary["agents"] = rows

    # Cases build fixtures: run them in a transaction that is always rolled back.
    with engine().connect() as conn:
        trans = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            outcomes = run_cases(s, selected)
        finally:
            s.close()
            trans.rollback()
    with session_scope() as s:
        store_outcomes(s, outcomes)
    summary["cases"] = [{"id": o.case_id, "passed": o.passed, "failures": o.failures, "error": o.error}
                        for o in outcomes]

    if json_out:
        console.print_json(json.dumps(summary, default=str))
    else:
        if "agents" in summary:
            console.print(f"  evaluated {summary['evaluated']} report(s); promoted {summary['promoted']['created']} "
                          f"new failure(s)")
            console.print(_agent_table(summary["agents"]))
        for o in outcomes:
            mark = "[green]PASS[/green]" if o.passed else "[red]FAIL[/red]"
            console.print(f"  {mark} {o.case_id} ({o.task})")
            for f in o.failures:
                console.print(f"        {f}")
    if any(not o.passed for o in outcomes):
        raise typer.Exit(1)
