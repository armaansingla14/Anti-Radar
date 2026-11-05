"""Underwriting categories: two explicit scales, documented thresholds, UNKNOWN when inputs are missing."""

from __future__ import annotations

from radar.diligence.deps import analyze_dependencies
from radar.diligence.infra import infra_review
from radar.diligence.oss import oss_health
from radar.diligence.security import security_review
from radar.diligence.underwriting import (
    RULES,
    benchmark_evidence,
    claim_verification,
    dependency_risk,
    open_source_health,
    security,
    underwrite,
)
from tests.fixtures.diligence.helpers import AS_OF, art, claim, facts, gh_file, repo, tree


def _claims(*statuses, ctype="PERFORMANCE"):
    out = []
    for i, s in enumerate(statuses, 1):
        c = claim(f"claim {i}", ctype, ordinal=i)
        c.status = s
        out.append(c)
    return out


def test_everything_unknown_without_inputs():
    deps = analyze_dependencies([], AS_OF)
    cats = underwrite([], [], conflicts=[], oss=oss_health([], facts(), AS_OF), deps=deps, sec=security_review([]),
                      infra=infra_review([], deps), ai=None)
    assert {c["key"]: c["rating"] for c in cats} == {k: "UNKNOWN" for k in RULES if k != "AI_SYSTEMS"}
    assert all(c["rule"] == RULES[c["key"]] for c in cats)
    assert {c["scale"] for c in cats if c["key"] in ("SECURITY", "OPEN_SOURCE_HEALTH", "DEPENDENCY_RISK")} == {"risk"}
    assert all(c["unknown"] or c["key"] == "BENCHMARK_EVIDENCE" for c in cats)


def test_claim_verification_thresholds():
    assert claim_verification(_claims("VERIFIED", "SUPPORTED", "UNVERIFIED"))["rating"] == "STRONG"
    mid = claim_verification(_claims("VERIFIED", "UNVERIFIED", "UNVERIFIED", "CONTRADICTED"))
    assert mid["rating"] == "WEAK"  # 25% verified/supported
    assert claim_verification(_claims("VERIFIED", "UNVERIFIED", "CONTRADICTED"))["rating"] == "MEDIUM"
    assert claim_verification(_claims("VERIFIED", "SUPPORTED", "SUPPORTED", "CONTRADICTED"))["rating"] == "MEDIUM"
    assert "2 of 3 material claims verified or supported" in claim_verification(
        _claims("VERIFIED", "SUPPORTED", "UNVERIFIED"))["headline"]


def test_security_and_dependency_ratings():
    env = gh_file(".env.example", "AWS_ACCESS_KEY_ID=" + "AKIA" + "Z7QWERTYUIOPASDF\n")
    assert security(security_review([env]))["rating"] == "HIGH"
    df = gh_file("Dockerfile", "FROM python:3.12\n")
    assert security(security_review([df]))["rating"] == "MODERATE"
    assert security(security_review([tree(["SECURITY.md"])]))["rating"] == "LOW"
    vuln = [gh_file("requirements.txt", "requests==2.19.0\n"),
            art("osv:batch", "osv", "registry", "", {"results": [{"ecosystem": "pypi", "name": "requests",
                                                                  "version": "2.19.0", "vulns": [{"id": "GHSA-1",
                                                                  "database_specific": {"severity": "HIGH"}}]}]})]
    assert dependency_risk(analyze_dependencies(vuln, AS_OF))["rating"] == "HIGH"
    clean = [gh_file("requirements.txt", "requests==2.32.0\n")]
    d = dependency_risk(analyze_dependencies(clean, AS_OF))
    assert d["rating"] == "LOW"
    assert any("checked for known vulnerabilities" in u for u in d["unknown"])  # no OSV result: unknown, not "safe"


def test_open_source_health_uses_concentration():
    con = art("contributors:acme-fixture/engine", "contributors", "code", "", {
        "full_name": "acme-fixture/engine", "contributors": [{"login": "a", "contributions": 90},
                                                             {"login": "b", "contributions": 10}]})
    assert open_source_health(oss_health([repo(), con], facts(), AS_OF))["rating"] == "HIGH"
    con.data["contributors"] = [{"login": x, "contributions": 20} for x in "abcde"]  # bus factor 3
    assert open_source_health(oss_health([repo(), con], facts(), AS_OF))["rating"] == "LOW"


def test_benchmark_evidence_stale_baseline_lowers_rating():
    t = tree(["benchmarks/run.py"])
    perf = _claims("SUPPORTED")
    assert benchmark_evidence(perf, [t], [])["rating"] == "STRONG"
    stale = [{"kind": "stale_benchmark_baseline", "summary": "old baseline"}]
    assert benchmark_evidence(perf, [t], stale)["rating"] == "MEDIUM"
    assert benchmark_evidence(_claims("UNVERIFIED"), [], [])["rating"] == "WEAK"
    assert benchmark_evidence([], [t], [])["rating"] == "UNKNOWN"
