"""Model comparison on the LLM-capable regression cases, normalized per (task, provider, model).

Only models whose provider has an API key are ever listed or run. The deterministic template is always a
candidate ("the template is enough" is a legitimate recommendation). With too little data the
recommendation is null and the reason says how much data exists.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.evals.cases import EvalCase, load_cases
from radar.evals.metrics import nearest_rank
from radar.evals.runner import TASKS, run_case, store_outcomes
from radar.llm.provider import BaseProvider
from radar.models import EvalCaseResult
from radar.settings import Settings, get_settings

PASS_MARGIN = 0.05
UNSUPPORTED_MARGIN = 0.02


@dataclass(frozen=True)
class ConfiguredModel:
    provider: str
    model: str
    tiers: tuple[str, ...]


def _custom_base_url(var: str) -> bool:
    """A gateway/base-URL override in the environment means the key belongs to some other deployment: never
    send evaluation traffic through it."""
    import os

    return bool((os.environ.get(var) or "").strip())


def configured_models(settings: Settings | None = None) -> list[ConfiguredModel]:
    """Models to compare: providers explicitly opted in via EVAL_PROVIDERS, with a key, and no base-URL override."""
    s = settings or get_settings()
    out: list[ConfiguredModel] = []
    opted_in = {p.strip().lower() for p in (getattr(s, "eval_providers", "") or "").split(",") if p.strip()}

    def add(provider: str, tiers: dict[str, str]) -> None:
        by_model: dict[str, list[str]] = defaultdict(list)
        for tier, model in tiers.items():
            if model:
                by_model[model].append(tier)
        out.extend(ConfiguredModel(provider, m, tuple(t)) for m, t in by_model.items())

    if "anthropic" in opted_in and s.anthropic_api_key and not _custom_base_url("ANTHROPIC_BASE_URL"):
        add("anthropic", {"fast": s.llm_fast_model, "standard": s.llm_standard_model, "deep": s.llm_deep_model})
    if "openai" in opted_in and s.openai_api_key and not _custom_base_url("OPENAI_BASE_URL"):
        add("openai", {"fast": s.openai_fast_model, "deep": s.openai_deep_model})
    return out


def pinned_provider(cm: ConfiguredModel, settings: Settings | None = None) -> BaseProvider:
    """A provider that answers every tier with ``cm.model`` (default SDK base URLs; never overridden here)."""
    s = settings or get_settings()
    if cm.provider == "anthropic":
        from radar.llm.anthropic_provider import AnthropicProvider as base

        key = s.anthropic_api_key
    elif cm.provider == "openai":
        from radar.llm.openai_provider import OpenAIProvider as base

        key = s.openai_api_key
    else:
        raise ValueError(f"unknown provider {cm.provider!r}")
    if not key:
        raise ValueError(f"no API key configured for {cm.provider}")
    model = cm.model
    cls = type(f"Pinned{base.__name__}", (base,), {"model_for": lambda self, tier: model,
                                                   "cache_models": lambda self, tier: [model]})
    return cls(api_key=key)


def _is_llm_case(c: EvalCase) -> bool:
    spec = TASKS.get(c.task)
    return bool(spec and spec.llm_capable)


def compare_models(session: Session, *, cases: list[EvalCase] | None = None,
                   models: list[ConfiguredModel] | None = None, include_baseline: bool = True,
                   providers: dict[str, BaseProvider] | None = None) -> uuid.UUID:
    """Run every LLM-capable case for the template baseline and each model; store one batch of results.

    ``providers`` (model name -> provider) overrides ``pinned_provider`` (tests use scripted fakes)."""
    cases = [c for c in (cases if cases is not None else load_cases()) if _is_llm_case(c)]
    models = configured_models() if models is None else models
    runs: list[tuple[BaseProvider | None, str, str]] = []
    if include_baseline:
        runs.append((None, "template", "template"))
    for cm in models:
        prov = (providers or {}).get(cm.model) or pinned_provider(cm)
        runs.append((prov, cm.provider, cm.model))
    outcomes = []
    for prov, pname, mname in runs:
        for c in cases:
            t0 = time.perf_counter()
            o = run_case(session, c, provider=prov)
            o.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            o.provider, o.model = pname, mname
            outcomes.append(o)
    return store_outcomes(session, outcomes)


def _metric(value: float | None, n: int, reason: str) -> dict[str, Any]:
    if value is None or n == 0:
        return {"value": None, "n": n, "reason": reason}
    return {"value": round(float(value), 4), "n": n, "reason": None}


def normalize(results: Iterable[EvalCaseResult]) -> list[dict[str, Any]]:
    """Per (task, provider, model): n, pass_rate, citation_validity, unsupported_claim_rate, coverage,
    latency_p50_ms, cost_per_case, structured_compliance (each {value, n, reason})."""
    groups: dict[tuple[str, str, str], list[EvalCaseResult]] = defaultdict(list)
    for r in results:
        groups[(r.task, r.provider, r.model)].append(r)
    out = []
    for (task, provider, model), rows in sorted(groups.items()):
        n = len(rows)
        m = [r.metrics or {} for r in rows]
        cv = [float(x["citation_validity"]) for x in m if x.get("citation_validity") is not None]
        cov = [float(x["coverage"]) for x in m if x.get("coverage") is not None]
        sv = [bool(x["structured_valid"]) for x in m if x.get("structured_valid") is not None]
        uns = sum(int(x.get("unsupported_claims") or 0) for x in m if x.get("claims") is not None)
        claims = sum(int(x.get("claims") or 0) for x in m if x.get("claims") is not None)
        lat = [float(r.latency_ms) for r in rows if r.latency_ms is not None]
        out.append({
            "task": task, "provider": provider, "model": model, "n": n,
            "pass_rate": _metric(sum(1 for r in rows if r.passed) / n, n, "no results"),
            "citation_validity": _metric(sum(cv) / len(cv) if cv else None, len(cv), "no cited claims recorded"),
            "unsupported_claim_rate": _metric(uns / claims if claims else None, claims, "no claims recorded"),
            "coverage": _metric(sum(cov) / len(cov) if cov else None, len(cov), "no coverage recorded"),
            "latency_p50_ms": _metric(nearest_rank(lat, 50), len(lat), "no latency recorded"),
            "cost_per_case": _metric(sum(float(r.cost_usd or 0.0) for r in rows) / n, n, "no results"),
            "structured_compliance": _metric(sum(sv) / len(sv) if sv else None, len(sv),
                                             "no structured output recorded"),
            "claims": claims,
        })
    return out


def _unsupported(row: dict[str, Any]) -> float | None:
    v = row["unsupported_claim_rate"]["value"]
    if v is None and row["claims"] == 0:
        return 0.0  # no claims at all: nothing unsupported
    return v


def recommend_models(session: Session, *, min_cases: int = 10, results: list[EvalCaseResult] | None = None
                     ) -> list[dict[str, Any]]:
    """Per task: the cheapest model whose quality is within the floor of the best (see docs)."""
    rows = normalize(results if results is not None else session.scalars(select(EvalCaseResult)))
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        spec = TASKS.get(r["task"])
        if spec is None or not spec.llm_capable:
            continue  # deterministic tasks have nothing to choose a model for
        by_task[r["task"]].append(r)
    out = []
    for task in sorted(by_task):
        cands = by_task[task]
        eligible = [r for r in cands if r["n"] >= min_cases]
        have = ", ".join(f"{r['model']}={r['n']}" for r in sorted(cands, key=lambda r: r["model"]))
        if len(eligible) < 2:
            out.append({"task": task, "recommendation": None,
                        "reason": f"insufficient evaluation data: {len(eligible)} model(s) with >= {min_cases} "
                                  f"cases (have: {have})"})
            continue
        # Best pass rate; among ties the lowest unsupported rate (the quality reference), then the cheapest.
        best = max(eligible, key=lambda r: (r["pass_rate"]["value"], -(_unsupported(r) or 0.0),
                                            -(r["cost_per_case"]["value"] or 0.0), r["model"]))
        best_pass = best["pass_rate"]["value"]
        best_uns = _unsupported(best)
        floor = [r for r in eligible if r["pass_rate"]["value"] >= best_pass - PASS_MARGIN
                 and (u := _unsupported(r)) is not None and best_uns is not None
                 and u <= best_uns + UNSUPPORTED_MARGIN]
        if best not in floor:
            floor.append(best)
        pick = min(floor, key=lambda r: (r["cost_per_case"]["value"] or 0.0,
                                         r["latency_p50_ms"]["value"] if r["latency_p50_ms"]["value"] is not None
                                         else float("inf"), r["model"]))
        pu = _unsupported(pick)
        reason = (f"{pick['model']}: pass {pick['pass_rate']['value']:.2f} (n={pick['n']}) vs best "
                  f"{best_pass:.2f}; unsupported {pu:.3f}" if pu is not None else
                  f"{pick['model']}: pass {pick['pass_rate']['value']:.2f} (n={pick['n']}) vs best {best_pass:.2f}")
        reason += (f"; ${pick['cost_per_case']['value']:.3f}/case vs ${best['cost_per_case']['value']:.3f} for "
                   f"{best['model']}")
        out.append({"task": task, "recommendation": {"provider": pick["provider"], "model": pick["model"]},
                    "reason": reason})
    return out
