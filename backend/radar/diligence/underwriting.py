"""Technical underwriting: one rating per category, never a single score.

Two scales, stated on every category:
* evidence scale — STRONG | MEDIUM | WEAK | UNKNOWN (how well public evidence backs the company's position);
* risk scale — LOW | MODERATE | HIGH | UNKNOWN (how much technical risk the evidence shows).

Each category records the deterministic threshold that produced its rating (``rule``) and splits what is
known, inferred and unknown. Missing inputs give UNKNOWN, not a guess.
"""

from __future__ import annotations

from typing import Any

from radar.diligence.evidence import benchmark_paths
from radar.diligence.taxonomy import ClaimType, Relation, Strength
from radar.diligence.taxonomy import VerificationStatus as VS
from radar.diligence.types import ArtifactDraft, ClaimDraft
from radar.diligence.vocab import is_source_path

EVIDENCE, RISK = "evidence", "risk"

TITLES = {
    "TECHNICAL_DIFFERENTIATION": "Technical differentiation",
    "ARCHITECTURE": "Architecture",
    "CLAIM_VERIFICATION": "Claim verification",
    "SECURITY": "Security",
    "OPEN_SOURCE_HEALTH": "Open-source health",
    "DEPENDENCY_RISK": "Dependency risk",
    "BENCHMARK_EVIDENCE": "Benchmark evidence",
    "SCALABILITY": "Scalability",
    "INFRASTRUCTURE": "Infrastructure",
    "AI_SYSTEMS": "AI systems",
}

RULES = {
    "TECHNICAL_DIFFERENTIATION": "STRONG: ≥3 capability/architecture/performance claims with direct code support; "
                                 "MEDIUM: 1–2; WEAK: code collected but none; UNKNOWN: no code tree.",
    "ARCHITECTURE": "STRONG: tree + languages + parsed manifests + a supported architecture claim; MEDIUM: tree + "
                    "languages + manifests; WEAK: only part of these; UNKNOWN: none collected.",
    "CLAIM_VERIFICATION": "STRONG: ≥60% of claims VERIFIED/SUPPORTED and none CONTRADICTED; MEDIUM: ≥30% and at most "
                          "one CONTRADICTED; WEAK: otherwise; UNKNOWN: no material claims.",
    "SECURITY": "HIGH: any HIGH finding; MODERATE: any MEDIUM finding or material question; LOW: artifacts reviewed, "
                "no MEDIUM+ finding; UNKNOWN: no code artifacts reviewed.",
    "OPEN_SOURCE_HEALTH": "HIGH: archived, top contributor ≥60% of commits, or no commits in 12 weeks; MODERATE: "
                          "bus factor ≤2, top contributor ≥40%, or no release in 365 days; LOW: otherwise; UNKNOWN: "
                          "no contributor or commit data.",
    "DEPENDENCY_RISK": "HIGH: a HIGH/CRITICAL OSV vulnerability or a deprecated runtime dependency; MODERATE: any OSV "
                       "vulnerability, unmaintained (>2y) runtime dependency or git/URL install; LOW: manifests parsed, "
                       "none of these; UNKNOWN: no manifests.",
    "BENCHMARK_EVIDENCE": "STRONG: a performance claim VERIFIED/SUPPORTED and a public benchmark harness; MEDIUM: a "
                          "harness exists or a performance claim is partially supported; WEAK: performance claims "
                          "without either; a stale benchmark baseline lowers the rating one level; UNKNOWN: no "
                          "performance claims.",
    "SCALABILITY": "STRONG: a scalability claim VERIFIED/SUPPORTED; MEDIUM: distributed-execution code present; WEAK: "
                   "scalability claims without code evidence; UNKNOWN: neither claims nor code.",
    "INFRASTRUCTURE": "HIGH: multi-cloud claimed but single provider in code; MODERATE: no CI workflows or no "
                      "container/deployment artifacts; LOW: CI and deployment artifacts present; UNKNOWN: no tree.",
    "AI_SYSTEMS": "HIGH: one hosted model provider and no switching evidence; MODERATE: no evaluation evidence; LOW: "
                  "switching/multi-provider (or self-hosted) and evaluation evidence; UNKNOWN: provider use not visible.",
}


def _cat(key: str, scale: str, rating: str, headline: str, known: list[str], inferred: list[str],
         unknown: list[str], evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"key": key, "title": TITLES[key], "scale": scale, "rating": rating, "headline": headline,
            "known": known, "inferred": inferred, "unknown": unknown, "evidence": (evidence or [])[:6],
            "rule": RULES[key]}


def _ev(e: dict[str, Any]) -> dict[str, Any]:
    return {"url": e.get("url", ""), "title": e.get("title", ""), "quote": e.get("quote", ""),
            "publisher": e.get("publisher", ""), "artifact_key": e.get("artifact_key", "")}


def _code_direct(c: ClaimDraft) -> list[dict[str, Any]]:
    return [e for e in c.evidence if e["publisher"] == "code" and e["relation"] == Relation.SUPPORTS
            and e["strength"] == Strength.DIRECT]


def technical_differentiation(claims: list[ClaimDraft], artifacts: list[ArtifactDraft]) -> dict[str, Any]:
    trees = [a for a in artifacts if a.kind == "tree"]
    if not trees:
        return _cat("TECHNICAL_DIFFERENTIATION", EVIDENCE, "UNKNOWN", "No public code was collected.", [], [],
                    ["Whether the core technology exists in public code is unknown."])
    relevant = [c for c in claims if c.claim_type in (ClaimType.TECHNICAL_CAPABILITY, ClaimType.ARCHITECTURE,
                                                      ClaimType.PERFORMANCE)]
    backed = [c for c in relevant if _code_direct(c)]
    n = len(backed)
    rating = "STRONG" if n >= 3 else "MEDIUM" if n >= 1 else "WEAK"
    kernels = sorted({p for t in trees for p in t.data.get("paths") or []
                      if is_source_path(p) and any(k in p.lower() for k in ("kernel", ".cu", ".hip", "triton", "csrc"))})
    known = [f"Claim {c.ordinal} has direct code support: {_code_direct(c)[0]['note']}." for c in backed]
    if kernels:
        known.append(f"Low-level kernel/native source files present ({len(kernels)}), e.g. {', '.join(kernels[:3])}.")
    unknown = [f"Claim {c.ordinal} ({c.claim_type.lower().replace('_', ' ')}) has no direct code support."
               for c in relevant if c not in backed]
    return _cat("TECHNICAL_DIFFERENTIATION", EVIDENCE, rating,
                f"{n} of {len(relevant)} technical claims have direct support in public code.", known, [], unknown,
                [_ev(e) for c in backed for e in _code_direct(c)[:1]])


def architecture(claims: list[ClaimDraft], artifacts: list[ArtifactDraft], deps: dict[str, Any]) -> dict[str, Any]:
    trees = [a for a in artifacts if a.kind == "tree"]
    langs = [a for a in artifacts if a.kind == "languages"]
    has_manifests = bool(deps.get("parsed"))
    if not (trees or langs or has_manifests):
        return _cat("ARCHITECTURE", EVIDENCE, "UNKNOWN", "No code artifacts collected.", [], [],
                    ["Languages, components and dependencies are unknown."])
    known: list[str] = []
    for la in langs:
        b = la.data.get("bytes") or {}
        total = sum(v for v in b.values() if isinstance(v, (int, float))) or 1
        top = sorted(b.items(), key=lambda kv: -kv[1])[:4]
        known.append(f"{la.data.get('full_name')}: " + ", ".join(f"{k} {v / total:.0%}" for k, v in top) + ".")
    for t in trees:
        top_dirs = sorted({p.split("/", 1)[0] for p in t.data.get("paths") or [] if "/" in p})[:12]
        if top_dirs:
            known.append(f"{t.data.get('full_name')} top-level directories: {', '.join(top_dirs)}.")
    if has_manifests:
        c = deps.get("counts") or {}
        known.append(f"{c.get('total', 0)} dependencies parsed from {len(deps.get('manifests') or [])} manifest(s).")
    arch_supported = [c for c in claims if c.claim_type == ClaimType.ARCHITECTURE
                      and c.status in (VS.VERIFIED, VS.SUPPORTED)]
    if trees and langs and has_manifests:
        rating = "STRONG" if arch_supported else "MEDIUM"
    else:
        rating = "WEAK"
    unknown = ["Runtime topology and internal service boundaries are not visible from the repository."]
    return _cat("ARCHITECTURE", EVIDENCE, rating, f"Architecture visible from {len(trees)} repository tree(s), "
                f"{len(langs)} language breakdown(s) and {'parsed' if has_manifests else 'no'} manifests.",
                known, [], unknown)


def claim_verification(claims: list[ClaimDraft]) -> dict[str, Any]:
    if not claims:
        return _cat("CLAIM_VERIFICATION", EVIDENCE, "UNKNOWN", "No material claims were extracted.", [], [],
                    ["The company's claims could not be identified from public pages."])
    n = len(claims)
    good = sum(1 for c in claims if c.status in (VS.VERIFIED, VS.SUPPORTED))
    contra = sum(1 for c in claims if c.status == VS.CONTRADICTED)
    r = good / n
    rating = "STRONG" if r >= 0.6 and contra == 0 else "MEDIUM" if r >= 0.3 and contra <= 1 else "WEAK"
    counts = {s: sum(1 for c in claims if c.status == s) for s in VS}
    known = [f"{v} {k.replace('_', ' ').lower()}" for k, v in counts.items() if v]
    unknown = [f"Claim {c.ordinal}: {c.status.replace('_', ' ').lower()}." for c in claims
               if c.status in (VS.UNVERIFIED, VS.UNTESTABLE)]
    return _cat("CLAIM_VERIFICATION", EVIDENCE, rating, f"{good} of {n} material claims verified or supported"
                + (f"; {contra} with an evidence conflict" if contra else "") + ".", known, [], unknown)


def security(sec: dict[str, Any]) -> dict[str, Any]:
    if not sec.get("reviewed"):
        return _cat("SECURITY", RISK, "UNKNOWN", "No code artifacts were reviewed.", [], [],
                    ["Security posture of public artifacts is unknown."])
    f = sec["findings"]
    counts = sec["counts"]
    mq = sec["material_questions"]
    rating = "HIGH" if counts["HIGH"] else "MODERATE" if counts["MEDIUM"] or mq else "LOW"
    known = [f"{x['id']} ({x['severity']}): {x['title']}" for x in f[:8]]
    posture = sec["posture"]
    if posture.get("security_md") is True:
        known.append("SECURITY.md disclosure policy present.")
    unknown = ["Runtime security controls, access management and incident history are not public."]
    return _cat("SECURITY", RISK, rating, f"{mq} material question(s); {counts['HIGH']} high, {counts['MEDIUM']} "
                f"medium, {counts['LOW']} low finding(s) in public artifacts.", known, [], unknown,
                [{"url": x["evidence"]["url"], "title": x["title"], "quote": x["evidence"]["quote"],
                  "publisher": "code", "artifact_key": x["evidence"]["artifact_key"]} for x in f[:6]])


def open_source_health(oss: dict[str, Any]) -> dict[str, Any]:
    m = oss.get("metrics") or {}
    if not oss.get("available") or (m.get("contributors", {}).get("value") is None
                                    and m.get("commits_12w", {}).get("value") is None):
        return _cat("OPEN_SOURCE_HEALTH", RISK, "UNKNOWN", "Contributor and commit data unavailable.", [], [],
                    oss.get("unknown") or ["Open-source health is unknown."])
    ids = {r["id"] for r in oss["risks"]}
    if ids & {"archived", "single_maintainer", "no_recent_commits"}:
        rating = "HIGH"
    elif ids & {"concentrated", "no_recent_release"}:
        rating = "MODERATE"
    else:
        rating = "LOW"
    known = [f"{k.replace('_', ' ')}: {v['value']} ({v['note']})" for k, v in m.items() if v["value"] is not None]
    headline = oss["risks"][0]["title"] if oss["risks"] else f"{oss['repo']}: no concentration or activity risk flagged."
    return _cat("OPEN_SOURCE_HEALTH", RISK, rating, headline, known[:14], [f"Maturity: {oss.get('maturity')}."],
                oss.get("unknown") or [], [{"url": r["url"], "title": r["title"], "quote": r["detail"],
                                            "publisher": "code", "artifact_key": ""} for r in oss["risks"]])


def dependency_risk(deps: dict[str, Any]) -> dict[str, Any]:
    if not deps.get("parsed"):
        return _cat("DEPENDENCY_RISK", RISK, "UNKNOWN", "No dependency manifests were collected.", [], [],
                    ["Dependencies and their health are unknown."])
    risks = deps["risks"]
    runtime = [r for r in risks if not r.get("dev")]
    high = [r for r in runtime if (r["id"] == "vulnerability" and r["severity"] == "HIGH") or r["id"] == "deprecated"]
    moderate = [r for r in runtime if r["id"] in ("vulnerability", "unmaintained", "git_install")]
    rating = "HIGH" if high else "MODERATE" if moderate else "LOW"
    c = deps["counts"]
    known = [f"{c['total']} dependencies ({c['runtime']} runtime, {c['pinned']} exactly pinned)."]
    known += [r["title"] for r in risks[:8]]
    unknown = []
    if deps.get("not_checked_unpinned"):
        unknown.append(f"{len(deps['not_checked_unpinned'])} unpinned runtime dependencies were not checked against "
                       "OSV (no exact version).")
    if not deps.get("osv_checked"):
        unknown.append("No exactly pinned dependencies were checked for known vulnerabilities.")
    llm = deps.get("llm_providers") or []
    inferred = [f"Hosted model providers: {', '.join(llm)}."] if llm else []
    return _cat("DEPENDENCY_RISK", RISK, rating, (risks[0]["title"] if risks else "No dependency risk indicators found."),
                known, inferred, unknown, [{"url": r.get("url", ""), "title": r["title"], "quote": r.get("detail", ""),
                                            "publisher": "registry", "artifact_key": r.get("artifact_key", "")}
                                           for r in risks[:6]])


_LOWER = {"STRONG": "MEDIUM", "MEDIUM": "WEAK", "WEAK": "WEAK", "UNKNOWN": "UNKNOWN"}


def benchmark_evidence(claims: list[ClaimDraft], artifacts: list[ArtifactDraft],
                       conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    perf = [c for c in claims if c.claim_type == ClaimType.PERFORMANCE]
    if not perf:
        return _cat("BENCHMARK_EVIDENCE", EVIDENCE, "UNKNOWN", "No performance claims to assess.", [], [], [])
    harness = sorted({p for t in artifacts if t.kind == "tree" for p in benchmark_paths(t)})[:5]
    good = [c for c in perf if c.status in (VS.VERIFIED, VS.SUPPORTED)]
    partial = [c for c in perf if c.status == VS.PARTIALLY_SUPPORTED]
    if good and harness:
        rating = "STRONG"
    elif harness or partial:
        rating = "MEDIUM"
    else:
        rating = "WEAK"
    stale = [c for c in conflicts if c["kind"] == "stale_benchmark_baseline"]
    if stale:
        rating = _LOWER[rating]
    known = [f"Claim {c.ordinal}: {c.status.replace('_', ' ').lower()} — {c.text}" for c in perf]
    if harness:
        known.append(f"Public benchmark harness: {', '.join(harness)}.")
    known += [c["summary"] for c in stale]
    unknown = ["No independent reproduction of the performance figures was found."] if not good else []
    return _cat("BENCHMARK_EVIDENCE", EVIDENCE, rating,
                f"{len(perf)} performance claim(s); {len(good)} verified/supported; "
                f"{'a public harness exists' if harness else 'no public harness'}"
                + ("; benchmark baseline is stale" if stale else "") + ".", known, [], unknown)


def scalability(claims: list[ClaimDraft], artifacts: list[ArtifactDraft]) -> dict[str, Any]:
    sc = [c for c in claims if c.claim_type == ClaimType.SCALABILITY]
    dist = sorted({p for t in artifacts if t.kind == "tree" for p in t.data.get("paths") or []
                   if is_source_path(p) and any(k in p.lower() for k in ("distributed", "parallel", "nccl", "rccl",
                                                                           "multinode", "multi_node", "sharding"))})[:5]
    if not sc and not dist:
        return _cat("SCALABILITY", EVIDENCE, "UNKNOWN", "No scalability claims and no distributed-execution code.",
                    [], [], ["Behaviour at scale is unknown."])
    good = [c for c in sc if c.status in (VS.VERIFIED, VS.SUPPORTED)]
    rating = "STRONG" if good else "MEDIUM" if dist else "WEAK"
    known = [f"Claim {c.ordinal}: {c.status.replace('_', ' ').lower()} — {c.text}" for c in sc]
    if dist:
        known.append(f"Distributed-execution code: {', '.join(dist)}.")
    return _cat("SCALABILITY", EVIDENCE, rating, f"{len(sc)} scalability claim(s); "
                f"{'distributed code present' if dist else 'no distributed code found'}.", known, [],
                ["No public measurement of behaviour at scale."])


def infrastructure(infra: dict[str, Any], conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    if not infra.get("available"):
        return _cat("INFRASTRUCTURE", RISK, "UNKNOWN", "No repository tree collected.", [], [], infra.get("unknown", []))
    single = [c for c in conflicts if c["kind"] == "multi_cloud_single_provider"]
    if single:
        rating = "HIGH"
    elif not infra["ci_workflows"] or not infra["deploy_artifacts"]:
        rating = "MODERATE"
    else:
        rating = "LOW"
    headline = single[0]["summary"] if single else (
        f"{len(infra['ci_workflows'])} CI workflow(s); deployment artifacts: "
        f"{', '.join(infra['deploy_artifacts']) or 'none'}.")
    return _cat("INFRASTRUCTURE", RISK, rating, headline, infra["known"], infra["inferred"], infra["unknown"])


def ai_systems(ai: dict[str, Any] | None) -> dict[str, Any] | None:
    if ai is None:
        return None
    states = {i["aspect"]: i["state"] for i in ai["items"]}
    providers, routers, runtimes = ai["llm_providers"], ai["routers"], ai["runtimes"]
    if not providers and not runtimes:
        rating = "UNKNOWN"
    elif len(providers) == 1 and not routers:
        rating = "HIGH"
    elif states.get("evaluation") != "known":
        rating = "MODERATE"
    else:
        rating = "LOW"
    known = [i["detail"] for i in ai["items"] if i["state"] == "known"]
    inferred = [i["detail"] for i in ai["items"] if i["state"] == "inferred"]
    headline = next((i["detail"] for i in ai["items"] if i["aspect"] == "provider_concentration"
                     and i["state"] == "known"), "Model-provider dependence is not fully visible.")
    return _cat("AI_SYSTEMS", RISK, rating, headline, known, inferred, ai["unknown"])


def underwrite(claims: list[ClaimDraft], artifacts: list[ArtifactDraft], *, conflicts: list[dict[str, Any]],
               oss: dict[str, Any], deps: dict[str, Any], sec: dict[str, Any], infra: dict[str, Any],
               ai: dict[str, Any] | None) -> list[dict[str, Any]]:
    cats = [
        technical_differentiation(claims, artifacts),
        architecture(claims, artifacts, deps),
        claim_verification(claims),
        security(sec),
        open_source_health(oss),
        dependency_risk(deps),
        benchmark_evidence(claims, artifacts, conflicts),
        scalability(claims, artifacts),
        infrastructure(infra, conflicts),
    ]
    a = ai_systems(ai)
    if a is not None:
        cats.append(a)
    return cats
