"""Infrastructure footprint from public artifacts: clouds, databases, deployment artifacts, CI, observability.

What is not visible stays unknown — production topology, SLAs and on-call are never public.
"""

from __future__ import annotations

import re
from typing import Any

from radar.diligence.types import ArtifactDraft

DEPLOY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Dockerfile", re.compile(r"(^|/)Dockerfile[\w.-]*$|\.dockerfile$", re.I)),
    ("Docker Compose", re.compile(r"(^|/)(docker-)?compose[\w.-]*\.ya?ml$", re.I)),
    ("Helm chart", re.compile(r"(^|/)Chart\.yaml$", re.I)),
    ("Kubernetes manifests", re.compile(r"(^|/)(k8s|kubernetes|manifests)/.+\.ya?ml$", re.I)),
    ("Terraform", re.compile(r"\.tf$", re.I)),
    ("Pulumi", re.compile(r"(^|/)Pulumi\.ya?ml$", re.I)),
    ("Serverless / Vercel / Fly", re.compile(r"(^|/)(serverless\.ya?ml|vercel\.json|fly\.toml|render\.yaml)$", re.I)),
)
CI_PATTERN = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$|^\.gitlab-ci\.yml$|^\.circleci/config\.yml$", re.I)


def infra_review(artifacts: list[ArtifactDraft], deps: dict[str, Any]) -> dict[str, Any]:
    trees = sorted([a for a in artifacts if a.kind == "tree"], key=lambda a: a.key)
    paths = [p for t in trees for p in t.data.get("paths") or []]
    services = deps.get("external_services") or []
    clouds = sorted({s["vendor"] for s in services if s["category"] == "cloud" and s["runtime"]})
    databases = sorted({s["vendor"] for s in services if s["category"] in ("database", "vector_db", "queue")})
    observability = sorted({s["vendor"] for s in services if s["category"] in ("observability", "llm_observability")})
    deploy = {label: sorted(p for p in paths if pat.search(p))[:5] for label, pat in DEPLOY_PATTERNS}
    deploy = {k: v for k, v in deploy.items() if v}
    ci = sorted(p for p in paths if CI_PATTERN.search(p))[:10]

    known, inferred, unknown = [], [], []
    if not trees:
        unknown.append("No repository tree was collected; infrastructure is unknown.")
    else:
        if ci:
            known.append(f"CI workflows: {len(ci)} ({', '.join(p.rsplit('/', 1)[-1] for p in ci[:4])}).")
        else:
            known.append("No CI workflow files in the public repository.")
        if deploy:
            known.append("Deployment artifacts: " + ", ".join(f"{k} ({len(v)})" for k, v in deploy.items()) + ".")
        else:
            known.append("No deployment artifacts (Dockerfile, Helm, Terraform, Kubernetes) in the public repository.")
    if clouds:
        known.append(f"Cloud SDKs in runtime dependencies: {', '.join(clouds)}.")
        if len(clouds) == 1:
            inferred.append(f"Single cloud provider in public code ({clouds[0]}).")
    if databases:
        known.append(f"Data stores / queues referenced: {', '.join(databases)}.")
    if observability:
        known.append(f"Observability: {', '.join(observability)}.")
    elif trees:
        unknown.append("No observability dependency (metrics/tracing/error reporting) in public manifests.")
    unknown += ["Production topology, redundancy and SLAs are not public.",
                "Hosting provider for the company's managed service (if any) is not public."]
    return {
        "available": bool(trees),
        "clouds": clouds,
        "databases": databases,
        "observability": observability,
        "deploy_artifacts": deploy,
        "ci_workflows": ci,
        "known": known,
        "inferred": inferred,
        "unknown": unknown,
    }
