"""Case runner: rollback, stored outcomes, model comparison with fake providers, the `radar eval` CLI."""

from __future__ import annotations

from sqlalchemy import func, select
from typer.testing import CliRunner

from radar.evals.cases import load_cases
from radar.evals.compare import ConfiguredModel, compare_models, normalize
from radar.evals.runner import latest_results, run_case, run_cases, store_outcomes
from radar.llm.provider import BaseProvider, LLMRefused
from radar.models import Company, EvalCaseResult, Person, ResearchReport, Signal


def _case(cid: str):
    return next(c for c in load_cases() if c.id == cid)


def test_run_case_rolls_back_its_fixtures(db):
    before = {m: db.scalar(select(func.count()).select_from(m)) for m in (Person, Company, Signal, ResearchReport)}
    for cid in ("affiliation-001", "template-brief-sourcing-001"):
        out = run_case(db, _case(cid))
        assert out.passed, out.failures
        assert out.provider == "template" and out.latency_ms is not None and out.metrics["task_completed"]
        assert out.metrics["citation_validity"] == 1.0 and out.metrics["structured_valid"] is True
    after = {m: db.scalar(select(func.count()).select_from(m)) for m in (Person, Company, Signal, ResearchReport)}
    assert after == before


def test_db_case_without_a_session_fails_cleanly():
    out = run_case(None, _case("affiliation-001"))
    assert not out.passed and out.error == "task needs a database"


def test_store_and_latest_results(db):
    pure = [c for c in load_cases() if c.task == "claim_checks"]
    first = store_outcomes(db, run_cases(None, pure))
    second = store_outcomes(db, run_cases(None, pure[:1]))
    latest = latest_results(db)
    assert set(latest) == {c.id for c in pure}
    assert latest[pure[0].id].batch_id == second and latest[pure[1].id].batch_id == first
    assert latest[pure[0].id].passed and "failures" in latest[pure[0].id].metrics


class Refusing(BaseProvider):
    """A 'live' provider pinned to a model name that always refuses (no network)."""

    name = "anthropic"
    available = True

    def __init__(self, model: str) -> None:
        self.model, self.calls = model, 0

    def model_for(self, tier):
        return self.model

    def _call_structured(self, model, tier, system, user, schema, max_tokens):
        self.calls += 1
        raise LLMRefused("scripted refusal")


def test_compare_models_with_a_fake_provider(db, monkeypatch):
    from radar.llm import provider as provider_mod

    monkeypatch.setattr(provider_mod, "check_daily_budget", lambda: None)
    fake = Refusing("fake-model")
    cases = [_case("affiliation-001")]
    batch = compare_models(db, cases=cases, models=[ConfiguredModel("anthropic", "fake-model", ("deep",))],
                           providers={"fake-model": fake})
    rows = list(db.scalars(select(EvalCaseResult).where(EvalCaseResult.batch_id == batch)))
    assert sorted((r.provider, r.model) for r in rows) == [("anthropic", "fake-model"), ("template", "template")]
    assert fake.calls == 1 and all(r.passed for r in rows)  # refusal falls back to the template, which passes
    norm = normalize(rows)
    assert [(n["provider"], n["n"]) for n in norm] == [("anthropic", 1), ("template", 1)]
    assert db.scalar(select(func.count()).select_from(Person)) == 0  # fixtures rolled back


def test_cli_single_case_compare_without_keys_and_unknown_case(db, monkeypatch):
    from radar.cli import app
    from radar.settings import get_settings

    runner = CliRunner()
    r = runner.invoke(app, ["eval", "--case", "affiliation-001"])
    assert r.exit_code == 0, r.output
    assert "PASS affiliation-001" in r.output
    assert db.scalar(select(func.count()).select_from(EvalCaseResult)) == 1
    assert db.scalar(select(func.count()).select_from(Person)) == 0

    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    r = runner.invoke(app, ["eval", "--compare-models"])
    assert r.exit_code == 0 and "no models configured" in r.output

    r = runner.invoke(app, ["eval", "--case", "nope-001"])
    assert r.exit_code == 2 and "unknown case" in r.output
    r = runner.invoke(app, ["eval", "--agent", "nobody"])
    assert r.exit_code == 2


def test_cli_default_run_evaluates_and_seeds(db):
    from radar.cli import app

    r = CliRunner().invoke(app, ["eval", "--agent", "resolution", "--seed-failures", "--json"])
    assert r.exit_code == 0, r.output
    assert "20 created" in r.output and '"bot-accounts-001"' in r.output
