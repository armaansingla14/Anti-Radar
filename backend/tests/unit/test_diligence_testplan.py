"""Validation plans: generated for testable claims, never executed."""

from __future__ import annotations

from radar.diligence.testplan import build_test_plan
from tests.fixtures.diligence.helpers import claim, package, page


def _plan(text, ctype, testability="HIGH", **kw):
    c = claim(text, ctype)
    c.testability = testability
    return build_test_plan(c, **kw)


def test_3x_faster_plan_has_baseline_hardware_metrics_and_pass_criteria():
    vllm = package("vllm", ["baseline"], "0.11.0", "2025-10-02T00:00:00+00:00")
    p = _plan("Acme delivers 3x higher throughput than vLLM 0.8 on H100 with Llama 3 70B.", "PERFORMANCE",
              packages={"vllm": vllm})
    assert p["baseline"]["name"] == "vLLM" and p["baseline"]["version_claimed"] == "0.8"
    assert "0.11.0" in p["baseline"]["version_guidance"] and "0.8" in p["baseline"]["version_guidance"]
    assert p["hardware"] == ["H100"] and p["models"] == ["Llama"]
    assert "output tokens/sec (aggregate)" in p["metrics"]
    assert any("≥ 3×" in x and "geometric-mean" in x for x in p["pass_criteria"])
    assert any("batch size 1, 8 and 32" in w for w in p["workloads"])
    assert any("≥ 5 times" in s for s in p["procedure"])
    assert any("disabled during the test run" in s for s in p["sandbox"])
    assert p["execution"] == {"status": "not_executed",
                              "reason": "plans are generated, not run; untrusted code is never executed on the radar host"}
    assert p["claim_id"] == 1


def test_missing_hardware_and_model_are_explicit():
    p = _plan("Acme is 2x faster than vLLM.", "PERFORMANCE")
    assert p["hardware"][0].startswith("Not stated by the company")
    assert p["models"][0].startswith("Not stated")
    assert "does not state the vLLM version" in p["baseline"]["version_guidance"]


def test_hardware_from_the_source_page_is_labelled():
    src = page("Benchmarks on 8x MI300X with Llama 3.1 70B.", "benchmarks")
    p = _plan("Acme reaches 2.8x the throughput of vLLM.", "PERFORMANCE", source=src)
    assert p["hardware"] == ["MI300X (named on the source page)"]
    assert p["models"] == ["Llama (named on the source page)"]


def test_memory_and_latency_facets():
    p = _plan("Cuts p95 latency by 40% and uses 30% less VRAM on H100.", "PERFORMANCE")
    assert "p95 latency (ms)" in p["metrics"] and "peak GPU memory (GB)" in p["metrics"]
    assert any("≥ 40%" in x for x in p["pass_criteria"])


def test_capability_plan_and_untestable_gets_none():
    p = _plan("Supports NVIDIA H100 and AMD MI300X.", "TECHNICAL_CAPABILITY")
    assert p["targets"] == ["H100", "MI300X"] and "installation success per target" in p["metrics"]
    assert _plan("SOC 2 Type II certified.", "SECURITY", testability="NOT_TESTABLE") is None
    assert _plan("Trusted by Fortune 500 companies.", "CUSTOMER_TRACTION", testability="LOW") is None
