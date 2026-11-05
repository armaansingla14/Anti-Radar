"""Regressions found by hand-auditing the first real diligence run (Unsloth AI, Nov 5, 2026).

Each test pins one failure filed in the agent failure ledger (DILIGENCE ANALYST); see radar/evals/failures.py.
"""

from __future__ import annotations

from radar.diligence.collect import _names
from radar.diligence.deps import analyze_dependencies
from radar.diligence.evidence import Context, m_capability
from radar.diligence.extract import classify, extract_claims, sentences
from radar.diligence.taxonomy import ClaimType
from tests.fixtures.diligence.helpers import AS_OF, art, claim, facts, gh_file, page, readme, tree


def _ctx(*arts):
    arts = list(arts)
    return Context.build(arts, facts(), AS_OF, analyze_dependencies(arts, AS_OF))


def test_generic_repo_names_are_not_search_terms_for_third_party_evidence():
    """'notebooks' (a repo of the company) pulled 'Show HN: Observable Notebooks' in as evidence."""
    arts = {k: art(k, "repo", "code", data={"full_name": f"unslothai/{n}"})
            for k, n in (("r1", "notebooks"), ("r2", "unsloth"), ("r3", "unsloth-zoo"), ("r4", "studio"))}
    names = _names(arts, facts("Unsloth AI"))
    assert "notebooks" not in names and "studio" not in names
    assert "Unsloth AI" in names and "Unsloth" in names


def test_licence_text_and_generic_words_are_not_capability_evidence():
    """A LICENSE file's word 'distributed' was counted as evidence for multi-GPU support."""
    c = claim("We support multi-GPU setups, NVIDIA and AMD GPUs.", ctype="SCALABILITY")
    lic = gh_file("LICENSE", "…or in the NOTICE text file distributed as part of the Derivative Works")
    prose = gh_file("docs/intro.md", "Our scheduler is distributed by design.")
    items = m_capability(c, _ctx(tree(["src/main.py"]), lic, prose))
    assert not [e for e in items if e.get("artifact_key") in (lic.key, prose.key) and not e.get("gap")]


def test_every_named_platform_is_a_facet_including_intel_cpu_and_mac():
    """'MacOS, Linux, Windows, NVIDIA, AMD, Intel and CPU' was SUPPORTED with no evidence for Intel or CPU."""
    c = claim("Unsloth supports MacOS, Linux, Windows, NVIDIA, AMD, Intel and CPU setups.")
    items = m_capability(c, _ctx(tree(["kernels/cuda_kernel.cu", "kernels/rocm_attn.hip", "scripts/win_setup.ps1",
                                       "core/sandbox_linux.py", "core/sandbox_macos.py"])))
    gaps = {e["note"] for e in items if e.get("gap")}
    assert "No public code evidence for Intel GPUs." in gaps and "No public code evidence for CPU." in gaps


def test_readme_credits_section_is_not_a_company_claim():
    """'The llama.cpp library that lets users run and save models with Unsloth' (a credits line) became ECONOMICS."""
    text = ("# Unsloth\n\nTrain models 2x faster with 70% less VRAM on NVIDIA GPUs.\n\n"
            "## Acknowledgements\n\nThe llama.cpp library that lets users run and save models with Unsloth.\n")
    quotes = [raw for _off, raw, _clean in sentences(readme(text))]
    assert not any("llama.cpp library" in q for q in quotes)
    assert any("2x faster" in q for q in quotes)
    assert classify("The llama.cpp library that lets users run and save models with Unsloth") != ClaimType.ECONOMICS
    assert classify("Save 60% on GPU costs versus on-demand clouds") == ClaimType.ECONOMICS


def test_the_same_claim_on_two_pages_is_one_ledger_entry():
    """The homepage and the docs stated the same platform list / model list / sandbox claim in different words."""
    home = page("Run nearly every model locally, with Day Zero support for Qwen, NVIDIA, Gemma and more.\n"
                "Unsloth supports MacOS, Linux, Windows, NVIDIA, AMD, Intel and CPU setups.\n"
                "Execute Bash and Python in a secure sandbox so models can run code and complete real tasks locally.")
    docs = page("Expect Day Zero support for models like Qwen, Gemma, NVIDIA models and more.\n"
                "Unsloth supports all OS including Mac, Windows, Linux and supports NVIDIA, Intel, AMD and Mac CPUs.\n"
                "Execute Bash and Python in a secure environment.", kind="docs", url="https://acme.example/docs")
    got = extract_claims([home, docs])
    texts = [c.text for c in got]
    assert sum("Day Zero" in t for t in texts) == 1, texts
    assert sum("Windows" in t for t in texts) == 1, texts
    assert sum("Bash and Python" in t for t in texts) == 1, texts
    merged = next(c for c in got if "Windows" in c.text)
    assert len(merged.company_evidence) == 2  # both pages remembered as sources


def test_setup_instructions_are_not_claims():
    """'Pick the llama.cpp build GGUF chat runs on during installation.' entered the ledger as a capability claim."""
    from radar.diligence.extract import materiality

    assert materiality("Pick the llama.cpp build GGUF chat runs on during installation.",
                       ClaimType.TECHNICAL_CAPABILITY, 1) is None
    assert materiality("Run and train nearly every model locally, with Day Zero support for Qwen and Gemma.",
                       ClaimType.TECHNICAL_CAPABILITY, 1) is not None


def test_open_source_cites_the_main_repository_and_reports_mixed_licensing():
    """The open-source claim was VERIFIED by an old side repo; the main repo's AGPL component went unmentioned."""
    from radar.diligence.evidence import m_open_source, sort_evidence
    from radar.diligence.questions import generate_questions
    from tests.fixtures.diligence.helpers import repo

    main = repo(stars=79000, license_spdx="Apache-2.0")
    main.data["full_name"], main.key = "unslothai/unsloth", "repo:unslothai/unsloth"
    side = repo(stars=2500, license_spdx="Apache-2.0")
    side.data["full_name"], side.key = "unslothai/hyperlearn", "repo:unslothai/hyperlearn"
    t = tree(["LICENSE", "studio/LICENSE.AGPL-3.0", "studio/backend/vendor/LICENSE.mit", "src/core.py"],
             name="unslothai/unsloth")
    c = claim("Unsloth is an open-source framework for training LLMs.", ctype="OPEN_SOURCE")
    items = sort_evidence(m_open_source(c, _ctx(side, main, t)))
    verifying = [e for e in items if e.get("verifies")]
    assert verifying[0]["artifact_key"] == "repo:unslothai/unsloth"
    mixed = [e for e in items if e["matcher"] == "mixed_licence"]
    assert len(mixed) == 1 and "AGPL-3.0" in mixed[0]["note"] and "studio/LICENSE.AGPL-3.0" in mixed[0]["quote"]
    c.evidence, c.ordinal, c.status = items, 1, "VERIFIED"
    qs = generate_questions([c], [], oss={}, deps={}, sec={}, ai=None, product="Unsloth")
    assert any("AGPL-3.0" in q["question"] and "commercial" in q["question"] for q in qs)


def test_underwriting_does_not_rate_strong_on_platform_support_or_paths_alone():
    """Technical differentiation and scalability were STRONG while every performance claim was unverified and the only
    scalability evidence was file paths."""
    from radar.diligence.underwriting import scalability, technical_differentiation

    t = tree(["kernels/fast_lora.py", "unsloth/kernels/rope_triton.py", "unsloth/distributed/fsdp2.py",
              "studio/backend/core/windows_setup.py"])
    plat = claim("Supports NVIDIA, AMD and Windows.", ordinal=1)
    plat.status, plat.evidence = "SUPPORTED", [{"relation": "supports", "strength": "direct", "publisher": "code",
                                                 "note": "src references NVIDIA", "gap": False}]
    perf = claim("Train 2x faster with 70% less VRAM.", ctype="PERFORMANCE", ordinal=2)
    perf.status, perf.evidence = "UNVERIFIED", []
    scale = claim("We support multi-GPU setups.", ctype="SCALABILITY", ordinal=3)
    scale.status, scale.evidence = "SUPPORTED", [{"relation": "supports", "strength": "direct", "publisher": "code",
                                                   "note": "paths", "gap": False}]
    td = technical_differentiation([plat, perf], [t])
    assert td["rating"] == "MEDIUM" and "performance claim" in td["headline"]
    assert scalability([scale], [t])["rating"] == "MEDIUM"


def test_dockerfile_heredoc_python_and_build_args_are_not_base_images():
    """Python 'from importlib.metadata import …' inside a RUN heredoc was reported as an unpinned base image."""
    from radar.diligence.deps import parse_dockerfile

    text = ("ARG BASE_IMAGE=nvidia/cuda:12.4.1-devel-ubuntu22.04\nFROM ${BASE_IMAGE}\n"
            "RUN python - <<'PY'\nfrom importlib.metadata import version\nprint(version('torch'))\nPY\n"
            "RUN pip install x \\\n    && python -c 'from studio.backend.core import export'\nUSER app\n")
    d = parse_dockerfile(text, "docker/Dockerfile")
    assert [f["image"] for f in d["from"]] == ["${BASE_IMAGE}"]
    assert d["from"][0]["latest"] is False and d["runs_as_root"] is False


def test_repeated_install_pipe_is_one_finding_and_policy_cites_the_main_repo():
    """The same 'curl … | sh' line was reported four times; the SECURITY.md finding cited a side repository."""
    from radar.diligence.security import security_review
    from tests.fixtures.diligence.helpers import repo

    big, small = repo(stars=79000), repo(stars=2500)
    big.data["full_name"], big.key, small.data["full_name"], small.key = "o/main", "repo:o/main", "o/aside", "repo:o/aside"
    cmd = "curl -fsSL https://example.ai/install.sh | sh"
    arts = [big, small, tree(["src/a.py"], name="o/main"), tree(["b.py"], name="o/aside"),
            readme(f"Install:\n\n{cmd}\n\nOr again: {cmd}\n"), page(f"Quick start: {cmd}")]
    found = security_review(arts)["findings"]
    pipes = [f for f in found if "pipe a remote script" in f["title"]]
    assert len(pipes) == 1
    policy = next(f for f in found if f["kind"] == "policy")
    assert "o/main" in policy["evidence"]["path"] or "o/main" in policy["evidence"]["url"]
