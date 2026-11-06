"""Reproducible validation plans for testable claims.

A plan says exactly how an analyst (or a future sandboxed runner) would test a claim: hypothesis, baseline and
version guidance, hardware, workload, metrics, procedure, pass criteria and sandbox requirements. Plans are
generated, never executed: untrusted code is never run on the radar host.
"""

from __future__ import annotations

import re
from typing import Any

from radar.diligence.taxonomy import ClaimType, Testability
from radar.diligence.types import ArtifactDraft, ClaimDraft, parse_dt
from radar.diligence.vocab import BASELINES, baseline_mentions, find_terms

NOT_EXECUTED = {
    "status": "not_executed",
    "reason": "plans are generated, not run; untrusted code is never executed on the radar host",
}

SANDBOX = [
    "Run inside a disposable container or VM with no access to the host filesystem.",
    "No secrets, credentials or cloud metadata endpoints reachable from the sandbox.",
    "Network access only while installing pinned dependencies; disabled during the test run.",
    "Explicit CPU, GPU, memory and wall-clock limits; the run is killed when a limit is reached.",
    "Every command, version and environment variable is logged with the results.",
]

_MULT = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?(?:x|×)(?![A-Za-z0-9])", re.I)
_PCT = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?%", re.I)


def _facets(claim: ClaimDraft) -> list[str]:
    t = claim.text.lower()
    f = []
    if re.search(r"throughput|tokens?/s|tok/s|tokens per second|req(?:uests)?/s|qps|faster|speed", t):
        f.append("throughput")
    if re.search(r"latency|ttft|time[- ]to[- ]first|\bms\b|responsive", t):
        f.append("latency")
    if re.search(r"memory|vram|footprint|\bgb\b", t):
        f.append("memory")
    if re.search(r"accura|quality|lossless|perplexity|benchmark score|eval", t):
        f.append("accuracy")
    if re.search(r"cost|cheaper|\$|price", t):
        f.append("cost")
    return f or ["throughput", "latency"]


METRICS: dict[str, list[str]] = {
    "throughput": ["output tokens/sec (aggregate)", "requests/sec at fixed concurrency"],
    "latency": ["p50 latency (ms)", "p95 latency (ms)", "time to first token (ms)"],
    "memory": ["peak GPU memory (GB)", "peak host memory (GB)"],
    "accuracy": ["task accuracy vs the baseline on the same eval set (absolute delta)"],
    "cost": ["cost per 1M output tokens at measured throughput and a stated hourly price"],
}


def _context_terms(claim: ClaimDraft, source: ArtifactDraft | None, category: str) -> list[str]:
    names = [t.name for t in find_terms(claim.text, (category,))]
    if not names and source is not None and source.text:
        names = [t.name for t in find_terms(source.text[:20000], (category,))][:4]
        return [f"{n} (named on the source page)" for n in names]
    return names


_COMPARED = re.compile(r"\b(?:than|vs\.?|versus|against|compared (?:to|with))\s+(?:(?:an?|the)\s+)?(?:equivalent\s+)?"
                       r"((?:[A-Z0-9][\w.+-]*|[a-z]+\d[\w.+-]*)(?:\s[A-Z0-9][\w.+-]*){0,2})")
_WORD_X = {"double": 2.0, "doubles": 2.0, "doubled": 2.0, "twice": 2.0, "triple": 3.0, "triples": 3.0, "tripled": 3.0}


def _baseline(claim: ClaimDraft, packages: dict[str, ArtifactDraft]) -> dict[str, Any] | None:
    mentions = baseline_mentions(claim.text)
    if not mentions:
        # The ledger keeps the strongest wording; the same claim's other quotes may name what it was measured against
        # ("… up to double the task throughput on Vera compared with an equivalent x86 GNR machine").
        texts = [claim.text, *(str(e.get("quote") or "") for e in claim.company_evidence)]
        m = next((m for t in texts if (m := _COMPARED.search(t))), None)
        if not m:
            return None
        return {"name": m.group(1), "version_claimed": None,
                "version_guidance": "The company does not state the baseline version; pin the latest stable release "
                                    "and ask the company which version their figure used."}
    name, ver, _span = mentions[0]
    pkg = packages.get(BASELINES[name][1] or "")
    latest = pkg.data.get("latest_version") if pkg else None
    latest_at = parse_dt(pkg.data.get("latest_release_at")) if pkg else None
    if ver and latest and latest != ver:
        guide = (f"Run against {name} {ver} (the version the company names) and against the latest release "
                 f"{latest}{' (' + latest_at.date().isoformat() + ')' if latest_at else ''}; report both.")
    elif ver:
        guide = f"Pin {name} {ver} as named by the company; also run the latest stable release."
    else:
        guide = (f"The company does not state the {name} version; pin the latest stable release"
                 f"{' (' + latest + ')' if latest else ''} and record it.")
    return {"name": name, "version_claimed": ver, "latest_version": latest, "version_guidance": guide}


def _pass_criteria(claim: ClaimDraft, facets: list[str]) -> list[str]:
    m, p = _MULT.search(claim.text), _PCT.search(claim.text)
    w = re.search(r"\b(" + "|".join(_WORD_X) + r")\b", claim.text, re.I)
    cond = ("under comparable conditions (same host size, container image, task mix and concurrency)" if _compute(claim)
            else "under comparable conditions (same hardware, model, precision/quantization, batch and sequence lengths)")
    if claim.claim_type == ClaimType.PERFORMANCE and (m or w):
        x = float(m.group(1)) if m else _WORD_X[w.group(1).lower()]
        metric = "throughput" if "throughput" in facets else facets[0]
        return [
            f"Supported if the geometric-mean {metric} ratio across the workload grid is ≥ {x:g}× the baseline {cond}.",
            f"Partially supported if ≥ {x:g}× holds on some workloads, or the overall ratio is between "
            f"{0.8 * x:.2g}× and {x:g}×.",
            f"Contradicted if the overall ratio is below {0.8 * x:.2g}× under the company's own stated configuration.",
        ]
    if claim.claim_type == ClaimType.PERFORMANCE and p:
        return [f"Supported if the measured improvement is ≥ {p.group(1)}% {cond}.",
                f"Contradicted if the improvement is below {0.8 * float(p.group(1)):.2g}% under the stated configuration."]
    if claim.claim_type == ClaimType.PERFORMANCE:
        return [f"Supported if the candidate beats the baseline on the primary metric {cond}; report the margin."]
    if claim.claim_type == ClaimType.TECHNICAL_CAPABILITY:
        return ["Supported if installation and an end-to-end smoke test succeed on every named target.",
                "Partially supported if some named targets pass and others fail or need undocumented steps."]
    if claim.claim_type == ClaimType.OPEN_SOURCE:
        return ["Supported if the repository builds from source under its stated license and the documented "
                "features work without proprietary components."]
    if claim.claim_type == ClaimType.CUSTOMER_TRACTION:
        return ["Supported if the re-measured public figure (with its date and source) is ≥ the claimed figure."]
    if claim.claim_type == ClaimType.SCALABILITY:
        return ["Supported if scaling efficiency stays ≥ 70% from 1 to the largest tested GPU/node count "
                "(throughput per GPU relative to 1 GPU)."]
    if claim.claim_type == ClaimType.ECONOMICS:
        return ["Supported if the measured cost per unit (with stated prices) is at or below the claimed figure."]
    return ["Supported if the documented behaviour is reproduced end to end, with logs."]


def _procedure(claim: ClaimDraft, baseline: dict | None) -> list[str]:
    steps = ["Pin and record exact versions of the candidate, the baseline, drivers and runtime (CUDA/ROCm).",
             "Use a fresh container per system under test; record the image digest."]
    if claim.claim_type in (ClaimType.PERFORMANCE, ClaimType.SCALABILITY, ClaimType.ECONOMICS):
        steps += [
            "Use the same container image, task mix and resource limits for candidate and baseline hosts."
            if _compute(claim) else "Use identical model weights, precision and quantization for candidate and baseline.",
            "Warm up for at least 1 minute (or 50 requests) before measuring.",
            "Run each configuration ≥ 5 times; report the median and interquartile range.",
            "Record exact commands, environment variables and hardware details (GPU model, count, interconnect).",
        ]
        if baseline:
            steps.append(f"Measure the baseline ({baseline['name']}) with its recommended production settings, "
                         "not defaults that handicap it.")
    elif claim.claim_type == ClaimType.TECHNICAL_CAPABILITY:
        steps += ["Follow only the public installation instructions on each named target.",
                  "Run a minimal end-to-end workload (e.g. load a model and serve one request) and log the output."]
    elif claim.claim_type == ClaimType.OPEN_SOURCE:
        steps += ["Clone into the sandbox, build from source and compare documented features with what builds.",
                  "List any component that requires a license key, a hosted service or a closed binary."]
    elif claim.claim_type == ClaimType.CUSTOMER_TRACTION:
        steps += ["Re-measure the public figure from its primary source (GitHub API / registry) and record the date."]
    else:
        steps += ["Exercise the documented behaviour end to end and log every command and response."]
    return steps


def _compute(claim: ClaimDraft) -> bool:
    """A general-compute claim (sandboxes, containers, CPU tasks), not a model-serving one: tokens/sec workloads
    would test the wrong thing."""
    t = claim.text
    return bool(re.search(r"\b(?:sandbox(?:es)?|containers?|CPUs?|tasks?|jobs?|builds?|grep|volumes?|storage|file ?systems?"
                          r"|disk|I/O)\b", t, re.I)) and not re.search(
        r"\b(?:tokens?|LLMs?|models?|inference|serving|training|fine-?tun)", t, re.I)


def _workloads(claim: ClaimDraft, models: list[str]) -> list[str]:
    if claim.claim_type == ClaimType.PERFORMANCE and _compute(claim):
        return ["a fixed mix of representative tasks (builds, file search, git operations, test suites)",
                "concurrency sweep from 1 task to host saturation", "the same container image and task mix on "
                "candidate and baseline hosts"]
    if claim.claim_type in (ClaimType.PERFORMANCE, ClaimType.ECONOMICS):
        return ["batch size 1, 8 and 32", "input/output lengths 128/128, 512/2048 and 2048/512 tokens",
                "concurrency sweep 1 → 64 requests"]
    if claim.claim_type == ClaimType.SCALABILITY:
        return ["1, 2, 4 and 8 GPUs (and 2 nodes if multi-node is claimed)", "fixed per-GPU batch size"]
    if claim.claim_type == ClaimType.TECHNICAL_CAPABILITY:
        return ["one minimal end-to-end request per named target" + (f" using {models[0]}" if models else "")]
    return []


def build_test_plan(claim: ClaimDraft, *, source: ArtifactDraft | None = None,
                    packages: dict[str, ArtifactDraft] | None = None) -> dict[str, Any] | None:
    """A validation plan for HIGH/MEDIUM testability claims (None otherwise)."""
    if claim.testability not in (Testability.HIGH, Testability.MEDIUM):
        return None
    facets = _facets(claim) if claim.claim_type in (ClaimType.PERFORMANCE, ClaimType.ECONOMICS) else []
    baseline = _baseline(claim, packages or {}) if claim.claim_type == ClaimType.PERFORMANCE else None
    measured = claim.claim_type in (ClaimType.PERFORMANCE, ClaimType.SCALABILITY, ClaimType.ECONOMICS)
    if measured:
        hardware = _context_terms(claim, source, "hardware")
        if _compute(claim) and not re.search(r"\bGPUs?\b", claim.text):
            hardware = [h for h in hardware if not h.endswith("GPUs")]  # "NVIDIA Vera CPUs" names a CPU, not GPUs
        hardware = hardware or [
            "Not stated by the company — choose hardware matching the target market and record it; ask the company "
            "which hardware produced their figure."]
        models = [] if _compute(claim) else _context_terms(claim, source, "model") or [
            "Not stated — the company's benchmark must specify model, size and precision."]
    else:
        hardware = [t.name for t in find_terms(claim.text, ("hardware",))]
        models = [t.name for t in find_terms(claim.text, ("model",))]
    targets = [t.name for t in find_terms(claim.text, ("hardware", "os", "framework", "model", "cloud"))]
    if claim.claim_type == ClaimType.PERFORMANCE and _compute(claim):
        metrics = ["completed tasks per minute at each concurrency level", "per-task latency p50/p95 (s)",
                   "CPU throttling and steal time"]
    elif claim.claim_type in (ClaimType.PERFORMANCE, ClaimType.ECONOMICS):
        metrics = [m for f in facets for m in METRICS[f]]
    elif claim.claim_type == ClaimType.TECHNICAL_CAPABILITY:
        metrics = ["installation success per target", "end-to-end smoke test pass/fail", "errors and warnings logged"]
    elif claim.claim_type == ClaimType.SCALABILITY:
        metrics = ["throughput per GPU", "scaling efficiency vs 1 GPU", "p95 latency at each scale"]
    elif claim.claim_type == ClaimType.CUSTOMER_TRACTION:
        metrics = ["the public figure, its source and measurement date"]
    elif claim.claim_type == ClaimType.OPEN_SOURCE:
        metrics = ["build from source success", "license of every component", "features requiring closed components"]
    elif claim.claim_type == ClaimType.SECURITY:
        metrics = ["observed network egress during use", "configuration defaults", "documented controls present"]
    else:
        metrics = ["documented behaviour reproduced (yes/no) with logs"]
    hypothesis = f"The claim holds as stated: “{claim.text}”"
    return {
        "claim_id": claim.ordinal,  # the claim's ordinal in the ledger
        "hypothesis": hypothesis,
        "facets": facets,
        "baseline": baseline,
        "candidate": {"description": "The company's public release, at a pinned version (record the tag or commit)."},
        "hardware": hardware,
        "models": models,
        "targets": targets if claim.claim_type == ClaimType.TECHNICAL_CAPABILITY else [],
        "metrics": metrics,
        "workloads": _workloads(claim, models),
        "procedure": _procedure(claim, baseline),
        "pass_criteria": _pass_criteria(claim, facets),
        "sandbox": list(SANDBOX),
        "execution": dict(NOT_EXECUTED),
    }
