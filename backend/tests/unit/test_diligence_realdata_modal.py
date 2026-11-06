"""Regressions found by hand-auditing the live diligence run on Modal (Nov 6, 2026), started from Portfolio Pulse.

Each test pins one failure filed in the agent failure ledger (DILIGENCE ANALYST); see radar/evals/failures.py. The
fixtures are synthetic pages modelled on the real text of modal.com (pricing, changelog, homepage mega-menu, blog).
"""

from __future__ import annotations

from types import SimpleNamespace

from radar.diligence.analyze import analyze
from radar.diligence.collect import collect_seeds
from radar.diligence.deps import analyze_dependencies
from radar.diligence.evidence import Context, m_open_source
from radar.diligence.extract import classify, extract_claims, not_a_claim, sentences
from radar.diligence.rules import decide_status
from radar.diligence.taxonomy import ClaimType
from tests.fixtures.diligence.helpers import AS_OF, art, claim, facts, page, repo, stub_fetcher

HEADER = {"id": None, "slug": "modal", "name": "Modal", "website": "https://modal.com", "github": None}

PRICING = """Compute costs
GPU Tasks Nvidia B300 $ 0.001972 0 . 0 0 1 9 7 2 / sec
Nvidia RTX PRO 6000 $ 0.000842 0 . 0 0 0 8 4 2 / sec
Network Egress $ 0.04 0 . 0 4 / GiB
$30 / month free compute
On Modal, a 3x multiplier is applied to the CPU and Memory list price for non-preemptible resources.
Sandboxes are billed at non-preemptible rates of 3x list price.
This job will cost $0.02365/day for the CPU, and $0.00200/day for the memory, adding up to $0.02565/day , or $0.77 over 30 days .
75 GPUs × 24 hrs × $3 / GPU-hr
Starter has no monthly fee and includes $30 of compute (GPUs, CPUs, Memory) per month.
Pay-as-you-go GPUs cost less than reserved GPUs for spiky workloads with idle periods each day.
"""

CHANGELOG = """Modal Changelog
DeepSeek V4.1 Flash 1017 is now available on Shared Endpoints
Access DeepSeek V4.1 Flash 1017 through a Shared Endpoint with per-token billing on Modal. This model is an updated \
checkpoint of DeepSeek V4.1 Flash, which improves coding, reasoning, and agentic capabilities; it includes support for \
text and image input and a 1M-token context window.
NVIDIA Nemotron 3.5 Super VL is now available on Modal
Deploy NVIDIA's Nemotron 3.5 Super VL as a Dedicated Endpoint on Modal. With 120B total parameters and 12B active per \
token, it supports visual reasoning across text, images, and video for document understanding.
Built for coding, reasoning, and agentic workloads, Beam has 501B total parameters, 23B active per token, and a \
1M-token context window.
We've recently made grep faster on Volumes v2 (~10x on cold caches and ~6x on warm caches).
"""

MENU = """Product
Inference Serve LLMs, image, video, and audio models, from real-time to batch
Modal Infrastructure Infrastructure rebuilt for AI, from the container runtime and storage to a multi-cloud capacity pool
GPU Glossary A fast guide to GPU concepts
"""

VERA = """All posts Back Engineering Erik Dunteman Oct 19, 2026
NVIDIA Vera CPUs double throughput for Modal Sandboxes
Last week, NVIDIA hand-delivered Modal's first Vera CPU server to our SF office.
Vera is NVIDIA's new Arm-compatible CPU, built for the Vera Rubin GPU platform but also available as a standalone CPU server.
Firstly, multi-tenant clusters are noisy.
Secondly, every second of CPU time costs more than just that CPU.
Our early benchmarking runs show up to double the task throughput on Vera compared with an equivalent x86 GNR machine, \
despite individual task latency measuring equal to the x86 machine.
"""


def test_price_tables_worked_examples_and_pricing_policy_are_not_claims():
    """Hand audit #1: price-list rows ('GPU Tasks Nvidia B300 $ 0.001972 0 .'), a worked cost example ('This job will
    cost …') and a billing rule ('a 3x multiplier is applied to the … list price') entered the ledger as ECONOMICS."""
    pricing = page(PRICING, kind="pricing", url="https://modal.com/pricing")
    got = extract_claims([pricing], company="Modal")
    econ = [c for c in got if c.claim_type == ClaimType.ECONOMICS]
    # At most one value assertion per pricing page; rows, arithmetic and policy never.
    assert len(econ) == 1 and econ[0].text.startswith("Pay-as-you-go GPUs cost less than reserved GPUs")
    texts = " ".join(c.text for c in got)
    assert not any(s in texts for s in ("$ 0.001972", "multiplier", "This job will cost", "× 24 hrs", "monthly fee",
                                        "billed at"))
    assert not_a_claim("GPU Tasks Nvidia B300 $ 0.001972 0 .", ClaimType.ECONOMICS) == "table or price-list fragment"
    assert not_a_claim("This job will cost $0.02365/day for the CPU, and $0.00200/day for the memory, adding up to "
                       "$0.02565/day.", ClaimType.ECONOMICS) == "worked cost example"
    assert not_a_claim("On Modal, a 3x multiplier is applied to the CPU and Memory list price for non-preemptible "
                       "resources.", ClaimType.ECONOMICS) == "pricing policy (a published fact)"
    # A value claim off the pricing page is still a claim.
    assert not_a_claim("Save 60% on GPU costs versus on-demand clouds.", ClaimType.ECONOMICS) is None


def test_third_party_model_catalog_entries_are_not_company_claims():
    """Hand audit #2: changelog entries about other vendors' models ('This model is an updated checkpoint of DeepSeek
    V4.1 Flash…', '<model> is now available on Modal') became the company's TECHNICAL_CAPABILITY claims."""
    log = page(CHANGELOG, kind="changelog", url="https://modal.com/changelog")
    texts = [c.text for c in extract_claims([log], company="Modal")]
    assert not any(t for t in texts if "DeepSeek" in t or "Nemotron" in t or "parameters" in t), texts
    assert any("grep faster" in t for t in texts)  # the company's own change stays
    for s in ("DeepSeek V4.1 Flash 1017 is now available on Shared Endpoints",
              "NVIDIA Nemotron 3.5 Super VL is now available on Modal"):
        assert not_a_claim(s, ClaimType.TECHNICAL_CAPABILITY, ("Modal",)) == "catalog availability line"
    assert not_a_claim("This model is an updated checkpoint of DeepSeek V4.1 Flash, which includes support for text "
                       "and image input.", ClaimType.TECHNICAL_CAPABILITY, ("Modal",)) is not None
    # A third-party product is fine when the company is the subject, and hardware support claims are untouched.
    assert not_a_claim("NVIDIA Vera CPUs double throughput for Modal Sandboxes", ClaimType.PERFORMANCE,
                       ("Modal",)) is None
    assert not_a_claim("Supports NVIDIA H100 and AMD MI300X out of the box.", ClaimType.TECHNICAL_CAPABILITY) is None


def test_navigation_label_is_not_glued_to_the_sentence_and_the_claim_is_architecture():
    """Hand audit #3: 'Modal Infrastructure Infrastructure rebuilt for AI, …' (a mega-menu card title glued to its
    description) was a TECHNICAL_CAPABILITY claim."""
    home = page(MENU, url="https://modal.com/")
    quotes = [raw for _off, raw, _clean in sentences(home)]
    assert "Infrastructure rebuilt for AI, from the container runtime and storage to a multi-cloud capacity pool" in quotes
    assert not any(q.startswith("Modal Infrastructure") for q in quotes)
    got = [c for c in extract_claims([home], company="Modal") if "rebuilt" in c.text]
    assert len(got) == 1 and got[0].claim_type == ClaimType.ARCHITECTURE
    assert got[0].source_quote in home.text and got[0].text.startswith("Infrastructure rebuilt")
    # "multi-cloud" with a supports-verb is still a capability claim.
    assert classify("Deploy on any cloud: AWS, GCP and Azure.") == ClaimType.TECHNICAL_CAPABILITY


def test_customer_reference_is_traction_and_blog_anecdotes_are_skipped():
    """Hand audit #4: 'Modal powers inference for the open-weight models that Snorkel is evaluating …' was typed
    OPEN_SOURCE; 'For example, when we wanted to train Qwen3.5-9B …' (a blog anecdote) was a MARKET claim."""
    snorkel = ("Modal powers inference for the open-weight models that Snorkel is evaluating as well as GPU and CPU "
               "sandboxes for millions of simulations per month.")
    assert classify(snorkel) == ClaimType.CUSTOMER_TRACTION
    assert classify("Designed to work out of the box with open source frameworks like slime and Miles.") \
        != ClaimType.OPEN_SOURCE
    assert classify("Acme is open-source under the MIT License.") == ClaimType.OPEN_SOURCE
    assert classify("Unsloth is an open-source framework for running and training LLMs.") == ClaimType.OPEN_SOURCE
    anecdote = ("For example, when we wanted to train Qwen3.5-9B on long-horizon software tasks, we only had a default "
                "16k-context config at the time.")
    assert not_a_claim(anecdote, ClaimType.MARKET, ("Modal",)) == "first-person anecdote"
    assert not_a_claim("Trusted by the world's leading AI teams", ClaimType.CUSTOMER_TRACTION, ("Modal",)) is not None


def test_open_source_claim_about_a_named_project_needs_that_projects_repository():
    """Found re-auditing the Modal ledger: 'Today we're releasing Spindle, an open-source … server' was VERIFIED by
    the licence of modal-labs/modal-examples, an unrelated repository."""
    examples = repo(stars=1290, license_spdx="MIT", name="modal-labs/modal-examples")
    ctx = Context.build([examples], facts("Modal"), AS_OF, analyze_dependencies([examples], AS_OF))
    c = claim("Today we're releasing Spindle, an open-source training server that runs in your Modal workspace.",
              ctype="OPEN_SOURCE")
    items = m_open_source(c, ctx)
    assert not any(e.get("verifies") for e in items) and any(e.get("gap") for e in items)
    assert decide_status(c, items, "HIGH")[0] == "UNVERIFIED"
    own = claim("Modal Examples is open source under the MIT License.", ctype="OPEN_SOURCE")
    assert any(e.get("verifies") for e in m_open_source(own, ctx))  # the named project's own repo still verifies
    generic = claim("Modal is open source.", ctype="OPEN_SOURCE")
    assert any(e.get("verifies") for e in m_open_source(generic, ctx))


def test_event_seed_page_is_collected_on_the_company_domain_only():
    """Hand audit #5: the post that triggered diligence ('NVIDIA Vera CPUs double throughput for Modal Sandboxes') was
    never collected — the crawl follows homepage links only and the blog index renders its list client-side."""
    post = "https://modal.com/blog/nvidia-vera-sandboxes"
    routes = {post: f"<html><head><title>Vera</title></head><body><h1>{VERA.splitlines()[1]}</h1>"
                    f"<p>{VERA.splitlines()[-1]}</p></body></html>",
              "https://evil.example/post": "<html><body>NVIDIA Vera CPUs triple throughput</body></html>"}
    f, stub = stub_fetcher(routes)
    company = SimpleNamespace(website="https://modal.com", name="Modal", slug="modal")
    arts: dict = {}
    collect_seeds(f, company, facts("Modal", domain="modal.com"), arts,
                  [post, "https://evil.example/post", "http://169.254.169.254/latest", "file:///etc/passwd", post])
    assert list(arts) == [f"web:{post}"]
    a = arts[f"web:{post}"]
    assert a.publisher == "company" and a.data == {"page_kind": "blog", "seed": True}
    assert "https://evil.example/post" not in stub.calls and not any("169.254" in c for c in stub.calls)
    refused = [t["error"] for t in f.trace if t.get("stage") == "seeds" and t.get("error")]
    assert any("not on the company's own domain" in e for e in refused) and any("unsafe" in e for e in refused)


def test_event_performance_claim_is_extracted_with_a_compute_test_plan():
    """Hand audit #5 (continued): once the seeded post is collected, the double-throughput claim is PERFORMANCE with a
    2× pass criterion, the stated x86 baseline and a sandbox-task workload; narrative and vendor lines are not claims."""
    seed = art("web:https://modal.com/blog/nvidia-vera-sandboxes", "web_page", "company", VERA,
               {"page_kind": "blog", "seed": True}, url="https://modal.com/blog/nvidia-vera-sandboxes")
    an = analyze([seed], facts("Modal"), as_of=AS_OF, company=HEADER)
    texts = [c.text for c in an.claims]
    perf = [c for c in an.claims if c.claim_type == ClaimType.PERFORMANCE]
    assert len(perf) == 1, texts  # the title and the benchmark sentence state one 2x figure: one claim
    p = perf[0]
    assert p.text == "NVIDIA Vera CPUs double throughput for Modal Sandboxes" and p.testability == "HIGH"
    assert len(p.company_evidence) == 2 and p.status == "UNVERIFIED"
    plan = p.test_plan
    assert plan["baseline"]["name"] == "x86 GNR" and "≥ 2×" in plan["pass_criteria"][0]
    assert "completed tasks per minute at each concurrency level" in plan["metrics"] and plan["models"] == []
    assert not any(h.endswith("GPUs") for h in plan["hardware"])
    assert not any(t.startswith(("Vera is", "Firstly", "Secondly", "Last week")) for t in texts), texts
