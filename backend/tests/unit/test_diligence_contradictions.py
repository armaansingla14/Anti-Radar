"""Each contradiction detector, with its label and the evidence it attaches to claims."""

from __future__ import annotations

from radar.diligence.contradictions import (
    detect,
    detect_multi_cloud_single_provider,
    detect_open_source_incomplete,
    detect_production_ready_no_releases,
    detect_stale_benchmark_baseline,
)
from radar.diligence.deps import analyze_dependencies
from radar.diligence.evidence import Context, attach_evidence
from tests.fixtures.diligence.helpers import AS_OF, claim, facts, gh_file, package, page, releases, repo, tree

VLLM = package("vllm", ["baseline"], "0.11.0", "2025-10-02T00:00:00+00:00", versions=[
    {"version": "0.8.0", "date": "2025-03-18T00:00:00+00:00", "prerelease": False},
    {"version": "0.8.5", "date": "2025-04-28T00:00:00+00:00", "prerelease": False},
    {"version": "0.11.0", "date": "2025-10-02T00:00:00+00:00", "prerelease": False},
])


def ctx(*arts):
    arts = list(arts)
    return Context.build(arts, facts(), AS_OF, analyze_dependencies(arts, AS_OF))


def test_stale_benchmark_baseline_reports_both_versions_and_dates():
    c = claim("Acme shows 2.8x the throughput of vLLM 0.8 on H100.", "PERFORMANCE")
    out = detect_stale_benchmark_baseline([c], ctx(VLLM))
    assert len(out) == 1
    d = out[0]["details"]
    assert d["claimed_version"] == "0.8" and d["claimed_version_date"] == "2025-03-18"
    assert d["latest_version"] == "0.11.0" and d["latest_date"] == "2025-10-02"
    assert out[0]["label"] == "CLAIM REQUIRES CLARIFICATION" and out[0]["claim_ordinals"] == [1]
    assert any(e["matcher"] == "stale_baseline" and e["relation"] == "contradicts" for e in c.evidence)


def test_stale_baseline_from_benchmark_page_applies_to_perf_claims_naming_it():
    c = claim("Acme is 3x faster than vLLM.", "PERFORMANCE")
    bench = page("Benchmarks used vLLM 0.8.0 on 8x H100.", kind="benchmarks", url="https://acme.example/benchmarks")
    out = detect_stale_benchmark_baseline([c], ctx(VLLM, bench))
    assert len(out) == 1 and out[0]["claim_ordinals"] == [1]


def test_recent_baseline_is_not_stale():
    c = claim("Acme is 3x faster than vLLM 0.11 on H100.", "PERFORMANCE")
    assert detect_stale_benchmark_baseline([c], ctx(VLLM)) == []


def test_fully_open_source_with_cloud_only_component():
    c = claim("Acme is fully open source.", "OPEN_SOURCE")
    pricing = page("The scheduler is available only in our cloud (Enterprise plan).", "pricing",
                   "https://acme.example/pricing")
    out = detect_open_source_incomplete([c], ctx(pricing))
    assert out and out[0]["kind"] == "open_source_incomplete" and "only in our cloud" in out[0]["evidence"][0]["quote"]
    plain = claim("Acme is open source.", "OPEN_SOURCE")  # open core is normal; only "fully" is questioned
    assert detect_open_source_incomplete([plain], ctx(pricing)) == []


def test_production_ready_without_stable_release():
    c = claim("Production-ready inference server.", "PRODUCT")
    x = ctx(repo(), releases([{"tag": "v0.3.0", "published_at": "2026-08-01T00:00:00Z"}]))
    attach_evidence([c], x)
    out = detect_production_ready_no_releases([c], x)
    assert out and out[0]["label"] == "CLAIM REQUIRES CLARIFICATION" and "pre-1.0" in out[0]["summary"]


def test_multi_cloud_single_provider():
    c = claim("Deploy on any cloud: AWS, GCP and Azure.")
    x = ctx(gh_file("requirements.txt", "boto3\n"), tree(["requirements.txt", "deploy/aws/main.tf"]))
    attach_evidence([c], x)
    out = detect_multi_cloud_single_provider([c], x)
    assert out and "AWS" in out[0]["summary"]


def test_detect_numbers_conflicts_and_puts_evidence_conflicts_first():
    stars = claim("Acme has over 10k GitHub stars.", "CUSTOMER_TRACTION", ordinal=2)
    perf = claim("Acme is 3x faster than vLLM 0.8.", "PERFORMANCE", ordinal=1)
    x = ctx(repo(stars=4000), VLLM)
    attach_evidence([perf, stars], x)
    out = detect([perf, stars], x)
    assert [c["id"] for c in out] == ["C1", "C2"]
    assert out[0]["label"] == "EVIDENCE CONFLICT" and out[0]["claim_ordinals"] == [2]
    assert out[1]["kind"] == "stale_benchmark_baseline"
