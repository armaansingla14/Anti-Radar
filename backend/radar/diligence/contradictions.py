"""Explicit contradiction detectors.

Each detector looks for one documented pattern of conflict between what the company says and what the public
artifacts show, and produces a conflict record labelled either

* ``EVIDENCE CONFLICT`` — a direct conflict with a verifiable artifact (license, measured metric), or
* ``CLAIM REQUIRES CLARIFICATION`` — the artifacts point the other way but cannot settle it.

Conflicts are never called deception. Detectors that find new evidence also attach it to the claims they
concern, so the status rules see it.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from radar.diligence.evidence import Context
from radar.diligence.taxonomy import CLARIFY_LABEL, CONFLICT_LABEL, ClaimType, Publisher, Relation, Strength
from radar.diligence.types import ArtifactDraft, ClaimDraft, days_between, evidence_item, parse_dt, version_key
from radar.diligence.vocab import BASELINES, baseline_mentions

FULLY_OPEN_RE = re.compile(r"\b(?:fully|100%|completely|entirely|truly|all of (?:it|our code) is)\s+open[- ]source",
                           re.I)
PROPRIETARY_RE = re.compile(
    r"\b(?:enterprise|pro|premium|cloud|commercial|paid)[- ](?:only|edition|plan|tier|version|license|feature)s?\b"
    r"|\bonly (?:available )?(?:in|on|with) (?:our )?(?:cloud|enterprise|managed|hosted|paid)\b"
    r"|\bclosed[- ]source\b|\bproprietary\b|\bnot open[- ]source(?:d)?\b|\bmanaged (?:service|version) only\b",
    re.I)
STALE_MINOR_GAP = 2  # minor releases behind
STALE_DAYS = 120  # or the latest release came this many days after the benchmarked one


def _sentence_around(text: str, start: int, end: int) -> str:
    s = max(text.rfind(". ", 0, start) + 2, text.rfind("\n", 0, start) + 1, 0)
    e_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    e = min(e_candidates) + 1 if e_candidates else len(text)
    return text[s:e].strip()[:300]


def _conflict(kind: str, label: str, claims: list[ClaimDraft], summary: str, evidence: list[dict],
              details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": "", "kind": kind, "label": label, "claim_ordinals": sorted({c.ordinal for c in claims}),
            "summary": summary, "evidence": evidence, "details": details or {}}


def detect_open_source_incomplete(claims: list[ClaimDraft], ctx: Context) -> list[dict]:
    """"Fully open source" while company pages describe proprietary / enterprise / cloud-only components."""
    targets = [c for c in claims if c.claim_type == ClaimType.OPEN_SOURCE and FULLY_OPEN_RE.search(c.text)]
    if not targets:
        return []
    found: list[dict] = []
    for art in ctx.artifacts:
        if art.publisher != Publisher.COMPANY or not art.text:
            continue
        for m in PROPRIETARY_RE.finditer(art.text):
            quote = _sentence_around(art.text, m.start(), m.end())
            found.append(evidence_item(art, relation=Relation.CONTRADICTS, strength=Strength.INDIRECT,
                                       matcher="open_source_incomplete", quote=quote,
                                       note="The company's own pages describe a component that is not open source."))
            break
        if len(found) >= 3:
            break
    if not found:
        return []
    for c in targets:
        c.evidence.extend(found)
    return [_conflict("open_source_incomplete", CLARIFY_LABEL, targets,
                      "Described as fully open source, but company pages mention proprietary, enterprise or "
                      "cloud-only components.", found)]


def detect_license_not_open(claims: list[ClaimDraft], ctx: Context) -> list[dict]:
    out = []
    for c in claims:
        bad = [e for e in c.evidence if e["matcher"] in ("open_source", "license")
               and e["relation"] == Relation.CONTRADICTS and e["strength"] == Strength.DIRECT]
        if bad:
            out.append(_conflict("license_mismatch", CONFLICT_LABEL, [c], bad[0]["note"], bad[:2]))
    return out


def detect_production_ready_no_releases(claims: list[ClaimDraft], ctx: Context) -> list[dict]:
    out = []
    for c in claims:
        bad = [e for e in c.evidence if e["matcher"] == "release_maturity" and e["relation"] == Relation.CONTRADICTS]
        if bad:
            out.append(_conflict("production_ready_no_stable_release", CLARIFY_LABEL, [c],
                                 f"Described as production ready, but {bad[0]['quote']}.", bad[:2],
                                 {"releases": bad[0]["quote"]}))
    return out


def detect_multi_cloud_single_provider(claims: list[ClaimDraft], ctx: Context) -> list[dict]:
    out = []
    for c in claims:
        bad = [e for e in c.evidence if e["matcher"] == "cloud_footprint" and e["relation"] == Relation.CONTRADICTS]
        if bad:
            out.append(_conflict("multi_cloud_single_provider", CLARIFY_LABEL, [c],
                                 "Multi-cloud deployment is claimed, but public code references a single provider: "
                                 f"{bad[0]['quote']}.", bad[:1]))
    return out


def _version_date(versions: list[dict], wanted: str) -> tuple[str | None, datetime | None]:
    """First published version matching ``wanted`` ('0.8' matches 0.8, 0.8.0, 0.8.1 — earliest wins)."""
    matches = [v for v in versions if str(v.get("version", "")) == wanted
               or str(v.get("version", "")).startswith(wanted + ".")]
    matches.sort(key=lambda v: version_key(v.get("version")))
    if not matches:
        return None, None
    return str(matches[0]["version"]), parse_dt(matches[0].get("date"))


def detect_stale_benchmark_baseline(claims: list[ClaimDraft], ctx: Context) -> list[dict]:
    """A benchmark names a baseline version that its registry shows has since been superseded."""
    packages = {str(a.data.get("name", "")).lower(): a for a in ctx.kind("package") if a.data.get("ecosystem") == "pypi"}
    sources: list[tuple[ArtifactDraft | None, str]] = [(None, c.source_quote) for c in claims
                                                       if c.claim_type == ClaimType.PERFORMANCE]
    sources += [(a, a.text) for a in ctx.artifacts if a.publisher == Publisher.COMPANY and a.text
                and re.search(r"bench|perf", f"{a.key} {(a.data or {}).get('page_kind', '')}", re.I)]
    seen: set[tuple[str, str | None]] = set()
    out = []
    for _art, text in sources:
        for name, ver, span in baseline_mentions(text):
            minor = ".".join(ver.split(".")[:2]) if ver else None
            if not ver or (name, minor) in seen:
                continue
            seen.add((name, minor))
            pkg_name = BASELINES[name][1]
            pkg = packages.get(pkg_name or "")
            if pkg is None:
                continue
            versions = [v for v in pkg.data.get("versions") or [] if not v.get("prerelease")]
            latest, latest_at = pkg.data.get("latest_version"), parse_dt(pkg.data.get("latest_release_at"))
            if not latest:
                continue
            claimed_ver, claimed_at = _version_date(versions, ver)
            lk, ck = version_key(latest), version_key(ver)
            if lk[:2] <= ck[:2]:
                continue
            gap_days = days_between(claimed_at, latest_at)
            stale = lk[0] != ck[0] or lk[1] - ck[1] >= STALE_MINOR_GAP or (gap_days is not None and gap_days >= STALE_DAYS)
            if not stale:
                continue
            when_claimed = f" (released {claimed_at.date().isoformat()})" if claimed_at else ""
            when_latest = f" on {latest_at.date().isoformat()}" if latest_at else ""
            summary = (f"Benchmark compares against {name} {ver}{when_claimed}; {name} {latest} was released"
                       f"{when_latest}.")
            ev = evidence_item(pkg, relation=Relation.CONTRADICTS, strength=Strength.INDIRECT,
                               matcher="stale_baseline", quote=f"latest {pkg_name} release: {latest}{when_latest}",
                               note=summary + " The comparison may not reflect the current baseline.")
            targets = [c for c in claims if c.claim_type == ClaimType.PERFORMANCE
                       and BASELINES[name][0].search(c.text)]
            for c in targets:
                c.evidence.append(dict(ev))
            out.append(_conflict("stale_benchmark_baseline", CLARIFY_LABEL, targets, summary, [ev], {
                "baseline": name, "package": pkg_name, "claimed_version": ver, "claimed_version_matched": claimed_ver,
                "claimed_version_date": claimed_at.date().isoformat() if claimed_at else None,
                "latest_version": latest, "latest_date": latest_at.date().isoformat() if latest_at else None,
                "quote": span,
            }))
    return out


def detect_direct_conflicts(claims: list[ClaimDraft], ctx: Context, covered: set[int]) -> list[dict]:
    """Any other direct contradiction found by a matcher (e.g. a measured metric below the claim)."""
    out = []
    for c in claims:
        if c.ordinal in covered:
            continue
        bad = [e for e in c.evidence if e["relation"] == Relation.CONTRADICTS and e["strength"] == Strength.DIRECT
               and e["publisher"] != Publisher.COMPANY]
        if bad:
            out.append(_conflict(f"{bad[0]['matcher']}_mismatch", CONFLICT_LABEL, [c], bad[0]["note"], bad[:2]))
    return out


DETECTORS = (detect_open_source_incomplete, detect_license_not_open, detect_production_ready_no_releases,
             detect_multi_cloud_single_provider, detect_stale_benchmark_baseline)


def detect(claims: list[ClaimDraft], ctx: Context) -> list[dict[str, Any]]:
    """Run every detector (after the evidence matchers); returns conflicts with stable ids C1…"""
    conflicts: list[dict[str, Any]] = []
    for det in DETECTORS:
        conflicts.extend(det(claims, ctx))
    covered = {o for c in conflicts if c["label"] == CONFLICT_LABEL for o in c["claim_ordinals"]}
    conflicts.extend(detect_direct_conflicts(claims, ctx, covered))
    conflicts.sort(key=lambda c: (c["label"] != CONFLICT_LABEL, c["claim_ordinals"][:1] or [999], c["kind"],
                                  c["summary"]))
    for i, c in enumerate(conflicts, 1):
        c["id"] = f"C{i}"
    return conflicts
