"""Public portfolio config: strict validation, private-field boundary, and the real config file."""

from __future__ import annotations

import copy

import pytest
import yaml

from radar.portfolio.config import (
    PRIVATE_KEYS,
    PortfolioConfigError,
    default_path,
    find_private_keys,
    load_config,
    parse_config,
)
from radar.settings import ROOT
from tests.conftest import FIXTURES

FIXTURE = FIXTURES / "portfolio" / "portfolio.yaml"
REAL = ROOT / "config" / "anti_fund_portfolio.yaml"


def _data() -> dict:
    return yaml.safe_load(FIXTURE.read_text())


def test_fixture_loads():
    cfg = load_config(FIXTURE)
    assert [c.name for c in cfg.companies] == ["Acme Robotics", "Halcyon Fusion"]
    assert cfg.companies[0].domain == "acme-robotics.com" and cfg.companies[0].ats.provider == "ashby"
    assert cfg.companies[1].public_stage is None  # unknown stays unknown


@pytest.mark.parametrize("path,key", [
    ("company", "ownership"), ("company", "cost_basis"), ("company", "marks"), ("company", "reserves"),
    ("comp", "board_seat"), ("root", "private_fields"), ("company", "internal_notes"),
])
def test_private_fields_refused_anywhere(path, key):
    d = _data()
    target = {"company": d["companies"][0], "comp": d["companies"][0]["comps"][0], "root": d}[path]
    target[key] = 1
    with pytest.raises(PortfolioConfigError, match="private investment fields"):
        parse_config(d)


def test_unknown_keys_rejected():
    d = _data()
    d["companies"][0]["linkedin"] = "x"
    with pytest.raises(PortfolioConfigError, match="linkedin"):
        parse_config(d)


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["companies"][0]["comps"][0].update(type="FRIEND"), "type"),
    (lambda d: d["companies"][0]["comps"][0].pop("source_url"), "source_url"),
    (lambda d: d["companies"][0]["comps"][0].update(source_url="ftp://x"), "source_url"),
    (lambda d: d["companies"][0]["comps"][0].update(confidence="SURE"), "confidence"),
    (lambda d: d["companies"][0].update(status="zombie"), "status"),
    (lambda d: d["companies"][0].update(ats={"provider": "workday", "slug": "x"}), "provider"),
    (lambda d: d["companies"][0].update(github_org="not a login!"), "GitHub org"),
    (lambda d: d["companies"].append(copy.deepcopy(d["companies"][0])), "duplicate"),
])
def test_invalid_values_rejected(mutate, msg):
    d = _data()
    mutate(d)
    with pytest.raises(PortfolioConfigError, match=msg):
        parse_config(d)


def test_missing_file_fails_clearly(tmp_path):
    with pytest.raises(PortfolioConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_find_private_keys_paths():
    assert find_private_keys({"a": [{"Ownership": 1}], "mark": 2}) == ["a[0].Ownership", "mark"]
    assert {"ownership", "cost_basis", "mark", "reserves"} <= PRIVATE_KEYS


@pytest.mark.skipif(not REAL.exists(), reason="config/anti_fund_portfolio.yaml not written yet")
def test_real_config_is_valid_and_public():
    raw = yaml.safe_load(REAL.read_text())
    assert find_private_keys(raw) == []
    cfg = load_config(REAL)
    assert default_path() == REAL
    assert cfg.source.url.startswith("https://") and cfg.companies
    for c in cfg.companies:
        assert c.domain and c.status in ("active", "acquired", "public", "exited")
        for comp in c.comps:
            assert comp.source_url.startswith(("https://", "http://"))
            assert comp.type in ("DIRECT", "ADJACENT", "INFRASTRUCTURE", "INCUMBENT", "OPEN_SOURCE")
            assert comp.confidence in ("HIGH", "MEDIUM", "LOW") and comp.why_comparable
