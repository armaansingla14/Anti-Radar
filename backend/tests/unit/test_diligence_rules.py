"""Status rules R1–R6 (evidence only, deterministic) and testability classification."""

from __future__ import annotations

import pytest

from radar.diligence.rules import RULES, classify_testability, decide_status, rule_id
from tests.fixtures.diligence.helpers import claim, ev


def _status(evidence, testability="HIGH"):
    s, reason, conf = decide_status(claim("x"), evidence, testability)
    return str(s), reason, str(conf)


def test_r1_direct_contradiction_wins_over_everything():
    s, reason, conf = _status([ev("code", "supports", verifies=True, key="a"),
                               ev("code", "contradicts", "direct", key="b")])
    assert s == "CONTRADICTED" and rule_id(reason) == "R1" and conf == "HIGH"
    s, _r, conf = _status([ev("third_party", "contradicts", "direct")])
    assert s == "CONTRADICTED" and conf == "MEDIUM"


def test_r1_ignores_company_and_indirect_contradictions():
    s, reason, _ = _status([ev("company", "contradicts", "direct")])
    assert s == "UNVERIFIED" and rule_id(reason) == "R6"
    s, reason, _ = _status([ev("code", "contradicts", "indirect")])
    assert s == "UNVERIFIED" and "require clarification" in reason


def test_r2_direct_verification():
    s, reason, conf = _status([ev("registry", "supports", "direct", verifies=True)])
    assert s == "VERIFIED" and rule_id(reason) == "R2" and conf == "HIGH"


def test_r2_blocked_by_gap_or_pending_clarification():
    s, reason, _ = _status([ev("code", "supports", "direct", verifies=True, key="a"),
                            ev("code", "context", "indirect", key="gap:H100", gap=True)])
    assert s == "PARTIALLY_SUPPORTED" and rule_id(reason) == "R4" and "no evidence for" in reason
    s, reason, _ = _status([ev("code", "supports", "direct", verifies=True, key="a"),
                            ev("company", "contradicts", "indirect", key="b")])
    assert s == "PARTIALLY_SUPPORTED" and rule_id(reason) == "R4"


def test_company_verification_never_counts():
    s, _r, _c = _status([ev("company", "supports", "direct", verifies=True)])
    assert s == "UNVERIFIED"


def test_r3_two_distinct_non_company_artifacts():
    s, reason, conf = _status([ev("code", "supports", "indirect", key="a"), ev("third_party", "supports", "indirect", key="b")])
    assert s == "SUPPORTED" and rule_id(reason) == "R3" and conf == "MEDIUM"
    s, _r, conf = _status([ev("code", "supports", "direct", key="a"), ev("code", "supports", "indirect", key="b")])
    assert s == "SUPPORTED" and conf == "HIGH"


def test_r3_needs_distinct_artifacts():
    s, reason, _ = _status([ev("code", "supports", "indirect", key="a"), ev("code", "supports", "direct", key="a")])
    assert s == "PARTIALLY_SUPPORTED" and "one supporting artifact" in reason


def test_r4_supports_mixed_with_indirect_conflict():
    s, reason, conf = _status([ev("code", "supports", "indirect", key="a"), ev("code", "supports", "indirect", key="b"),
                               ev("registry", "contradicts", "indirect", key="c")])
    assert s == "PARTIALLY_SUPPORTED" and rule_id(reason) == "R4" and conf == "LOW"


def test_r5_untestable_and_r6_unverified():
    assert _status([], "NOT_TESTABLE")[0] == "UNTESTABLE"
    assert _status([ev("company", "supports", "indirect")], "NOT_TESTABLE")[0] == "UNTESTABLE"
    s, reason, conf = _status([], "LOW")
    assert s == "UNVERIFIED" and rule_id(reason) == "R6" and conf == "LOW"


def test_context_items_never_count():
    s, _r, _c = _status([ev("third_party", "context", "indirect", key="a"), ev("code", "context", "direct", key="b")])
    assert s == "UNVERIFIED"


def test_every_rule_documented():
    assert list(RULES) == ["R1", "R2", "R3", "R4", "R5", "R6"]


@pytest.mark.parametrize("text,ctype,expected", [
    ("Acme is 3x faster than vLLM on H100.", "PERFORMANCE", "HIGH"),
    ("Acme is faster than vLLM.", "PERFORMANCE", "MEDIUM"),
    ("State-of-the-art performance.", "PERFORMANCE", "LOW"),
    ("Supports NVIDIA H100 and AMD MI300X.", "TECHNICAL_CAPABILITY", "HIGH"),
    ("Acme is open source under Apache 2.0.", "OPEN_SOURCE", "HIGH"),
    ("Over 10k GitHub stars.", "CUSTOMER_TRACTION", "HIGH"),
    ("Trusted by 500+ companies.", "CUSTOMER_TRACTION", "NOT_TESTABLE"),
    ("Used by Stripe and Shopify.", "CUSTOMER_TRACTION", "LOW"),
    ("Written in Rust.", "ARCHITECTURE", "HIGH"),
    ("Built on a custom scheduler.", "ARCHITECTURE", "MEDIUM"),
    ("SOC 2 Type II certified.", "SECURITY", "NOT_TESTABLE"),
    ("Data is encrypted at rest.", "SECURITY", "MEDIUM"),
    ("Scales to 1,000 GPUs.", "SCALABILITY", "MEDIUM"),
    ("Scales effortlessly.", "SCALABILITY", "LOW"),
    ("Saves 60% on inference cost.", "ECONOMICS", "MEDIUM"),
    ("The fastest engine in the world.", "MARKET", "NOT_TESTABLE"),
    ("Windows support coming soon.", "TECHNICAL_CAPABILITY", "NOT_TESTABLE"),
])
def test_testability(text, ctype, expected):
    assert classify_testability(claim(text, ctype)) == expected
