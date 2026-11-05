"""Regression / comparison cases: one YAML file per case in ``tests/evals/cases`` (``RADAR_EVAL_CASES_DIR``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from radar.evals.taxonomy import CATEGORY_DEFINITIONS, resolve_agent_alias

CASES_DIR = Path(__file__).resolve().parents[2] / "tests" / "evals" / "cases"
REQUIRED = ("id", "title", "agent", "task", "failure_category", "input", "expected")


class CaseError(ValueError):
    pass


@dataclass
class EvalCase:
    id: str
    title: str
    agent: str  # canonical agent / component name
    task: str
    failure_category: str
    input: dict[str, Any]
    expected: dict[str, Any]
    min_citations: int | None = None
    expected_epistemic: dict[str, str] = field(default_factory=dict)
    regression_for: str | None = None  # the failure (source_ref or number) this case guards
    llm_capable: bool = False
    path: Path | None = None


def cases_dir() -> Path:
    env = os.environ.get("RADAR_EVAL_CASES_DIR")
    return Path(env) if env else CASES_DIR


def validate_case(d: dict[str, Any], *, path: Path | None = None) -> EvalCase:
    from radar.evals.runner import TASKS

    where = path.name if path else "<case>"
    if not isinstance(d, dict):
        raise CaseError(f"{where}: a case must be a mapping")
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise CaseError(f"{where}: missing {', '.join(missing)}")
    if d["failure_category"] not in CATEGORY_DEFINITIONS:
        raise CaseError(f"{where}: unknown failure_category {d['failure_category']!r}")
    if d["task"] not in TASKS:
        raise CaseError(f"{where}: unknown task {d['task']!r} (known: {', '.join(sorted(TASKS))})")
    agent = resolve_agent_alias(str(d["agent"]))
    if agent is None:
        raise CaseError(f"{where}: unknown agent {d['agent']!r}")
    if not isinstance(d["input"], dict) or not isinstance(d["expected"], dict):
        raise CaseError(f"{where}: input and expected must be mappings")
    return EvalCase(
        id=str(d["id"]), title=str(d["title"]), agent=agent, task=str(d["task"]),
        failure_category=str(d["failure_category"]), input=d["input"], expected=d["expected"],
        min_citations=d.get("min_citations"), expected_epistemic=dict(d.get("expected_epistemic") or {}),
        regression_for=d.get("regression_for"), llm_capable=bool(TASKS[d["task"]].llm_capable), path=path,
    )


def load_cases(directory: Path | str | None = None) -> list[EvalCase]:
    """Every ``*.yaml`` case, validated, sorted by id. Raises CaseError (with the filename) on any problem."""
    root = Path(directory) if directory else cases_dir()
    out: list[EvalCase] = []
    seen: dict[str, str] = {}
    for p in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text())
        except yaml.YAMLError as e:
            raise CaseError(f"{p.name}: invalid YAML: {e}") from None
        case = validate_case(data, path=p)
        if case.id in seen:
            raise CaseError(f"{p.name}: duplicate case id {case.id!r} (also in {seen[case.id]})")
        seen[case.id] = p.name
        out.append(case)
    return sorted(out, key=lambda c: c.id)
