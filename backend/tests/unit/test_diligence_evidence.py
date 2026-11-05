"""Evidence matchers: license, open source, capability vs code, language, traction, maturity, maintenance,
multi-cloud, third-party lexical cues."""

from __future__ import annotations

from radar.diligence.deps import analyze_dependencies
from radar.diligence.evidence import (
    Context,
    m_capability,
    m_language,
    m_license,
    m_maintenance,
    m_multicloud,
    m_open_source,
    m_production,
    m_third_party,
    m_traction,
)
from tests.fixtures.diligence.helpers import AS_OF, art, claim, facts, gh_file, package, releases, repo, tree


def ctx(*arts, **kw):
    arts = list(arts)
    return Context.build(arts, facts(**kw), AS_OF, analyze_dependencies(arts, AS_OF))


def rel(items):
    return [(e["relation"], e["strength"], e["verifies"]) for e in items]


def test_license_verifies_or_contradicts():
    c = claim("Acme is licensed under the Apache 2.0 license.", "OPEN_SOURCE")
    assert rel(m_license(c, ctx(repo(license_spdx="Apache-2.0")))) == [("supports", "direct", True)]
    assert rel(m_license(c, ctx(repo(license_spdx="BUSL-1.1")))) == [("contradicts", "direct", False)]
    assert rel(m_license(c, ctx(repo(license_spdx=None)))) == [("context", "indirect", False)]
    assert m_license(c, ctx()) == []  # no repository: nothing to compare


def test_open_source_requires_osi_license():
    c = claim("Acme is open source.", "OPEN_SOURCE")
    assert rel(m_open_source(c, ctx(repo(license_spdx="MIT")))) == [("supports", "direct", True)]
    assert rel(m_open_source(c, ctx(repo(license_spdx="Elastic-2.0")))) == [("contradicts", "direct", False)]
    assert m_open_source(c, ctx()) == []


def test_capability_direct_from_source_paths_indirect_from_config_and_gap_when_missing():
    c = claim("Supports NVIDIA H100 and AMD MI300X out of the box.")
    t = tree(["kernels/rocm/attn_gfx942.hip", "docs/h100.md", ".github/workflows/ci.yml"])
    ci = gh_file(".github/workflows/ci.yml", "runs-on: [self-hosted, mi300x]")
    items = m_capability(c, ctx(t, ci))
    mi = [e for e in items if "MI300X" in e["note"]]
    assert ("supports", "direct", False) in rel(mi)  # a .hip source file: direct support, not verification
    assert ("supports", "indirect", False) in rel(mi)  # the CI config
    h100 = [e for e in items if "H100" in e["note"]]
    assert all(e["strength"] == "indirect" or e.get("gap") for e in h100)  # only a docs path, never direct
    assert all(not e["verifies"] for e in items)


def test_capability_gap_marker():
    c = claim("Runs on Windows and macOS.")
    items = m_capability(c, ctx(tree(["src/main.rs"])))
    assert {e["note"] for e in items if e.get("gap")} == {"No public code evidence for Windows.",
                                                         "No public code evidence for macOS."}


def test_readme_mentions_are_not_code_evidence():
    c = claim("Supports Intel Gaudi accelerators.")
    rd = art("readme:acme-fixture/engine", "readme", "company", "Works on Intel Gaudi 3.")
    items = m_capability(c, ctx(rd, tree(["src/main.rs"])))
    assert all(e.get("gap") for e in items)


def test_capability_on_performance_claims_is_context_only():
    c = claim("Acme is 3x faster than vLLM on MI300X.", "PERFORMANCE")
    items = m_capability(c, ctx(tree(["kernels/mi300x_attn.hip"])))
    assert items and all(e["relation"] == "context" for e in items)


def test_language_share():
    c = claim("Acme is written in Rust.", "ARCHITECTURE")
    langs = art("languages:acme-fixture/engine", "languages", "code", "",
                {"full_name": "acme-fixture/engine", "bytes": {"Rust": 800, "Python": 200}})
    assert rel(m_language(c, ctx(langs))) == [("supports", "direct", True)]
    langs.data["bytes"] = {"Python": 1000, "Rust": 5}
    assert rel(m_language(c, ctx(langs))) == [("contradicts", "direct", False)]


def test_traction_measured_vs_claimed():
    over = claim("Acme has over 10k GitHub stars.", "CUSTOMER_TRACTION")
    assert rel(m_traction(over, ctx(repo(stars=12000)))) == [("supports", "direct", True)]
    low = m_traction(over, ctx(repo(stars=4100)))
    assert rel(low) == [("contradicts", "direct", False)] and "2026-10-06" in low[0]["note"]
    near = m_traction(over, ctx(repo(stars=9000)))
    assert rel(near) == [("supports", "indirect", False)]
    dl = claim("Acme has 20,000 monthly downloads on PyPI.", "CUSTOMER_TRACTION")
    pkg = package("acme", ["own"], "1.0.0", downloads={"last_day": 1, "last_week": 10, "last_month": 25000})
    assert rel(m_traction(dl, ctx(pkg))) == [("supports", "direct", True)]
    total = claim("Acme has 1M downloads.", "CUSTOMER_TRACTION")  # no period: not measurable from recent stats
    assert m_traction(total, ctx(pkg)) == []


def test_contributor_cap_never_contradicts():
    c = claim("Built by 500 contributors.", "CUSTOMER_TRACTION")
    con = art("contributors:acme-fixture/engine", "contributors", "code", "",
              {"full_name": "acme-fixture/engine", "capped": True,
               "contributors": [{"login": f"u{i}", "contributions": 1} for i in range(100)]})
    assert all(e["relation"] != "contradicts" for e in m_traction(c, ctx(repo(), con)))


def test_production_ready_vs_releases():
    c = claim("Production-ready inference server.", "PRODUCT")
    none = m_production(c, ctx(repo(), releases([])))
    assert rel(none) == [("contradicts", "indirect", False)] and "no tagged releases" in none[0]["note"]
    pre = m_production(c, ctx(repo(), releases([{"tag": "v0.4.1", "published_at": "2026-09-01T00:00:00Z"}])))
    assert rel(pre) == [("contradicts", "indirect", False)]
    ok = m_production(c, ctx(repo(), releases([{"tag": "v1.2.0", "published_at": "2026-09-01T00:00:00Z"}])))
    assert rel(ok) == [("supports", "indirect", False)]


def test_maintenance_push_age():
    c = claim("Acme is actively maintained.", "OPEN_SOURCE")
    assert rel(m_maintenance(c, ctx(repo(pushed_days=5)))) == [("supports", "direct", True)]
    assert rel(m_maintenance(c, ctx(repo(pushed_days=400)))) == [("contradicts", "direct", False)]
    assert rel(m_maintenance(c, ctx(repo(pushed_days=90)))) == [("context", "indirect", False)]


def test_multicloud_single_provider_requires_clarification():
    c = claim("Deploy on any cloud: AWS, GCP and Azure.")
    files = [gh_file("requirements.txt", "boto3==1.34.0\n"), tree(["requirements.txt"])]
    out = m_multicloud(c, ctx(*files))
    assert rel(out) == [("contradicts", "indirect", False)] and "AWS" in out[0]["note"]
    files2 = [gh_file("requirements.txt", "boto3==1.34.0\ngoogle-cloud-storage==2.0\n"), tree(["requirements.txt"])]
    assert rel(m_multicloud(c, ctx(*files2))) == [("supports", "indirect", False)]


def test_third_party_lexical_cues_are_never_direct():
    c = claim("Acme supports AMD MI300X.")
    pos = art("hn-comment:1", "hn_comment", "third_party", "Acme works great on our MI300X boxes.")
    neg = art("hn-comment:2", "hn_comment", "third_party", "Acme doesn't work on MI300X for me.")
    unclear = art("hn-comment:3", "hn_comment", "third_party", "Has anyone tried Acme on MI300X?")
    unrelated = art("hn-comment:4", "hn_comment", "third_party", "MI300X is a nice GPU.")  # product not named
    out = {e["artifact_key"]: e for e in m_third_party(c, ctx(pos, neg, unclear, unrelated))}
    assert out["hn-comment:1"]["relation"] == "supports"
    assert out["hn-comment:2"]["relation"] == "contradicts"
    assert out["hn-comment:3"]["relation"] == "context"
    assert "hn-comment:4" not in out
    assert all(e["strength"] == "indirect" and not e["verifies"] for e in out.values())


def test_third_party_performance_needs_performance_words():
    c = claim("Acme is 3x faster than vLLM on MI300X.", "PERFORMANCE")
    works = art("hn-comment:1", "hn_comment", "third_party", "Acme works great on our MI300X boxes.")
    assert [e["relation"] for e in m_third_party(c, ctx(works))] == ["context"]
