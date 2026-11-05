"""Evaluation taxonomy and agent registry."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from radar.evals.taxonomy import (
    AGENTS,
    CATEGORY_DEFINITIONS,
    COMPONENTS,
    FailureCategory,
    agent_for_kind,
    agent_for_purpose,
    resolve_agent_alias,
)

RADAR = Path(__file__).resolve().parents[2] / "radar"


def test_every_category_has_a_definition():
    assert len(FailureCategory) == 13  # the 12 required categories + RANKING_ERROR
    assert set(CATEGORY_DEFINITIONS) == set(FailureCategory)
    assert all(v.strip() for v in CATEGORY_DEFINITIONS.values())


@pytest.mark.parametrize("alias,name", [
    ("analyst", "RADAR ANALYST"), ("meeting", "MEETING BRIEF"), ("memo", "ANTI MEMO"),
    ("diligence", "DILIGENCE ANALYST"), ("morning", "MORNING BRIEF"), ("RADAR ANALYST", "RADAR ANALYST"),
    ("radar analyst", "RADAR ANALYST"), ("formation", "FORMATION SCORER"), ("meet", "MEET RANKER"),
])
def test_aliases_resolve(alias, name):
    assert resolve_agent_alias(alias) == name


def test_unknown_alias_is_none():
    assert resolve_agent_alias("nobody") is None and resolve_agent_alias(None) is None


def test_every_kind_and_purpose_maps_back_to_its_agent():
    for a in AGENTS:
        if a.kind:
            assert agent_for_kind(a.kind) is a
        for p in a.purposes:
            assert agent_for_purpose(p) is a
    assert agent_for_kind("unknown") is None and agent_for_purpose("x.y") is None
    assert all(c.deterministic and not c.purposes for c in COMPONENTS)


def test_registered_purposes_exist_in_the_code():
    """A grep-equivalent check: every purpose string the registry claims is used somewhere in radar/."""
    text = "\n".join(p.read_text(errors="ignore") for p in RADAR.rglob("*.py") if "evals" not in p.parts)
    for a in AGENTS:
        for purpose in a.purposes:
            if purpose == "diligence.extract" and importlib.util.find_spec("radar.diligence.extract") is None:
                continue  # the diligence package is optional while it is being built
            assert f'"{purpose}"' in text, purpose
