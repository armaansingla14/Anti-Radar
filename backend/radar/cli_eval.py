"""`radar eval …` commands (registered in radar/cli.py)."""

from __future__ import annotations

import typer

eval_app = typer.Typer(help="Agent evaluation: metrics, failures, regression cases, model comparison.", no_args_is_help=True)
