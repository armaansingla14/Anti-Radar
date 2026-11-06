"""`radar demo capture --update --portfolio` plumbing (no network: the collectors are replaced)."""

from __future__ import annotations

import gzip
import json

from radar import ops
from radar.providers.base import NormalizedSignal, ProviderBatch
from tests import portfolio_scenario as sc


def test_capture_appends_to_todays_update_and_keeps_config(db, tmp_path, monkeypatch):
    from radar import cli

    monkeypatch.setattr("radar.portfolio.config.default_path", lambda: sc.CONFIG)
    target = tmp_path / "2026-10-06"
    target.mkdir()
    with gzip.open(target / "0001_github.json.gz", "wt") as f:  # a batch captured earlier the same day
        json.dump({"provider": "github", "params": {}, "run": {}, "batch": {"provider": "github"}}, f)
    (target / "MANIFEST.json").write_text(json.dumps({"as_of": "2026-10-06", "sources": ["github"], "note": "n"}))
    seen = {}

    def fake_capture(targets):
        seen["targets"] = [(t.name, t.role, t.ats, t.usaspending_recipient) for t in targets]
        sig = NormalizedSignal(entity=targets[0].ref, signal_type="company_post", source_type="website",
                               source_url="https://acme-robotics.com/news/x", title="Introducing X",
                               event_at=sc.at(10, 5))
        return [ops._run("website", lambda: ProviderBatch(provider="website", signals=[sig]), {"portfolio": "t"})]

    monkeypatch.setattr(ops, "capture_portfolio", fake_capture)
    cli._capture_portfolio(target, tmp_path / "2026-10-06.tmp", None)
    names = sorted(p.name for p in target.iterdir())
    assert names == ["0001_github.json.gz", "0002_website.json.gz", "MANIFEST.json", "portfolio.yaml"]
    m = json.loads((target / "MANIFEST.json").read_text())
    assert m["files"] == 2 and "usaspending" in m["sources"] and "github" in m["sources"]
    assert m["portfolio"]["companies"] == 2 and m["note"] == "n"
    roles = {(n, r) for n, r, _, _ in seen["targets"]}
    assert ("Acme Robotics", "portfolio") in roles and ("Orbitform", "comp") in roles
    assert len(seen["targets"]) == 4  # Orbitform once although it is a comp of both portfolio companies
    assert ("Halcyon Fusion", "portfolio", None, "HALCYON FUSION, INC.") in seen["targets"]


def test_hn_terms_require_distinctive_names():
    assert ops.portfolio_hn_terms("Aeon", "aeonindustrial.com", None) == ["aeonindustrial.com"]
    assert ops.portfolio_hn_terms("General Matter", "generalmatter.com", None) == ["generalmatter.com",
                                                                                   "General Matter"]
    assert ops.portfolio_hn_terms("Hugging Face", "huggingface.co", "huggingface") == ["huggingface", "Hugging Face"]
