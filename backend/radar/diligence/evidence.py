"""Evidence matchers: attach public evidence for and against each claim.

Every matcher is a small, named, deterministic function over the collected artifacts. Matchers only emit
non-company evidence (code, registry, third party); the company's own statements are already recorded as
``company_evidence`` by extraction, so a claim can never support itself.

Strength semantics:
* ``direct`` — the artifact speaks to the claim itself (the license GitHub reports; a measured star count; a
  source file implementing the named target);
* ``indirect`` — consistent but not conclusive (a CI config naming the target; release maturity for a
  "production ready" claim; a third-party comment).

``verifies`` is set only where the artifact settles the claim (license, measured public metrics, language
breakdown, push recency). Code that *targets* a platform supports a capability claim; it does not verify it.
Third-party text is matched with simple lexical cues and is never direct: when uncertain it is ``context``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from radar.diligence.taxonomy import ClaimType, Publisher, Relation, Strength
from radar.diligence.types import (
    ArtifactDraft,
    ClaimDraft,
    CompanyFacts,
    days_between,
    evidence_item,
    fmt_num,
    gap_item,
    parse_dt,
    parse_quantity,
    parse_version,
    proper_names,
)
from radar.diligence.vocab import Term, baseline_mentions, find_terms, is_source_path, path_matches

MAX_EVIDENCE_PER_CLAIM = 25
TRACTION_TOLERANCE = 0.8  # measured < 80% of the claimed figure contradicts it
ACTIVE_PUSH_DAYS = 30
STALE_PUSH_DAYS = 180

OSI_LICENSES = frozenset({
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0",
    "MPL-2.0", "ISC", "Unlicense", "EPL-2.0", "0BSD", "Zlib", "BSL-1.0", "MIT-0", "UPL-1.0", "PostgreSQL",
    "GPL-2.0-only", "GPL-3.0-only", "GPL-2.0-or-later", "GPL-3.0-or-later", "LGPL-3.0-only", "AGPL-3.0-only",
})
NON_OPEN_LICENSES = frozenset({"BUSL-1.1", "Elastic-2.0", "SSPL-1.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0"})
LICENSE_FAMILIES: dict[str, re.Pattern[str]] = {
    "Apache": re.compile(r"\bApache\b", re.I),
    "MIT": re.compile(r"\bMIT\b"),
    "BSD": re.compile(r"\bBSD\b"),
    "AGPL": re.compile(r"\bAGPL", re.I),
    "LGPL": re.compile(r"\bLGPL", re.I),
    "GPL": re.compile(r"(?<![AL])\bGPL", re.I),
    "MPL": re.compile(r"\bMPL\b|\bMozilla Public License\b", re.I),
    "BUSL": re.compile(r"\bBUSL\b|\bBusiness Source License\b", re.I),
}

OPEN_SOURCE_RE = re.compile(r"\bopen[- ]source[ds]?\b", re.I)
PRODUCTION_RE = re.compile(r"\bproduction[- ](?:ready|grade)\b|\bbattle[- ]tested\b|\benterprise[- ]ready\b"
                           r"|\bstable (?:release|api)\b|\bgenerally available\b", re.I)
MAINTAINED_RE = re.compile(r"\bactively (?:maintained|developed)\b|\bactive development\b|\bregularly updated\b"
                           r"|\bfrequent releases\b|\bweekly releases\b", re.I)
MULTICLOUD_RE = re.compile(r"\bmulti[- ]cloud\b|\bany cloud\b|\bcloud[- ]agnostic\b|\ball (?:major )?clouds\b"
                           r"|\bdeploy anywhere\b|\bevery (?:major )?cloud\b", re.I)
LANGUAGE_CLAIM_RE = re.compile(r"\b(?:written|implemented|built) (?:in|with)\b|\bpure\b|\b100%\b|\bnative\b", re.I)
TRACTION_METRIC_RE = re.compile(
    r"(\d+(?:[.,]\d+)*\s?(?:k|m|b|thousand|million|billion)?\+?)\s+(?:monthly |weekly |daily |total )?"
    r"(?:GitHub |PyPI |npm )?(stars|downloads|installs|contributors|forks)\b"
    r"(?:\s+(?:per|a|each|every)\s+(day|week|month))?", re.I)
NEGATIVE_CUES = re.compile(
    r"\b(?:slower|doesn'?t work|does not work|didn'?t work|couldn'?t reproduce|could not reproduce|can'?t reproduce"
    r"|not reproducible|misleading|cherry[- ]picked|worse than|broken|crash(?:es|ed)?|not (?:actually )?faster"
    r"|fails? to)\b", re.I)
POSITIVE_CUES = re.compile(
    r"\b(?:reproduced|confirmed|can confirm|i got|we got|in (?:my|our) (?:tests|benchmarks)|works? (?:great|well)"
    r"|independently verified)\b", re.I)
PERF_WORDS = re.compile(r"\b(?:faster|slower|throughput|latency|tok(?:ens)?/s|tokens per second|benchmark|speed)\b"
                        r"|(?<![A-Za-z0-9.])\d+(?:\.\d+)?\s?x\b", re.I)
CLOUD_PATH_ALIASES = {"AWS": ("aws", "eks", "ecs", "sagemaker", "lambda"), "Google Cloud": ("gcp", "gke", "gcloud"),
                      "Azure": ("azure", "aks")}


@dataclass
class Context:
    """Indexed view of the artifacts for the matchers."""

    artifacts: list[ArtifactDraft]
    facts: CompanyFacts
    as_of: datetime
    deps: dict[str, Any] = field(default_factory=dict)
    by_kind: dict[str, list[ArtifactDraft]] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, artifacts: list[ArtifactDraft], facts: CompanyFacts, as_of: datetime,
              deps: dict[str, Any] | None = None) -> Context:
        ctx = cls(sorted(artifacts, key=lambda a: a.key), facts, as_of, deps or {})
        for a in ctx.artifacts:
            ctx.by_kind.setdefault(a.kind, []).append(a)
        repos = ctx.repos()
        names = {facts.name}
        names.update(r.data.get("full_name", "").split("/")[-1] for r in repos)
        names.update(str(p.data.get("name")) for p in ctx.kind("package") if "own" in (p.data.get("roles") or []))
        ctx.names = sorted({n for n in names if n and len(n) >= 3}, key=str.lower)
        return ctx

    def kind(self, k: str) -> list[ArtifactDraft]:
        return self.by_kind.get(k, [])

    def repos(self) -> list[ArtifactDraft]:
        """Repository metadata artifacts, most-starred first."""
        return sorted(self.kind("repo"), key=lambda a: (-(a.data.get("stars") or 0), a.key))

    def for_repo(self, kind: str, full_name: str) -> ArtifactDraft | None:
        return next((a for a in self.kind(kind) if a.key == f"{kind}:{full_name}"), None)

    def third_party(self) -> list[ArtifactDraft]:
        return [a for a in self.artifacts if a.publisher == Publisher.THIRD_PARTY and a.text]


# ── matchers ─────────────────────────────────────────────────────────────────


def m_license(claim: ClaimDraft, ctx: Context) -> list[dict]:
    families = [f for f, p in LICENSE_FAMILIES.items() if p.search(claim.text)]
    if not families or not re.search(r"licen[sc]|open[- ]source|\bApache\b|\bMIT\b", claim.text, re.I):
        return []
    out = []
    for repo in ctx.repos():
        spdx = repo.data.get("license_spdx")
        name = repo.data.get("full_name")
        if not spdx or spdx == "NOASSERTION":
            out.append(evidence_item(repo, relation=Relation.CONTEXT, strength=Strength.INDIRECT, matcher="license",
                                     note=f"GitHub does not identify a standard license for {name}."))
            continue
        if any(spdx.upper().startswith(f.upper()) for f in families):
            out.append(evidence_item(repo, relation=Relation.SUPPORTS, strength=Strength.DIRECT, verifies=True,
                                     matcher="license", quote=f"license: {spdx}",
                                     note=f"GitHub reports {name} is licensed {spdx}."))
        else:
            out.append(evidence_item(repo, relation=Relation.CONTRADICTS, strength=Strength.DIRECT, matcher="license",
                                     quote=f"license: {spdx}",
                                     note=f"The company names {'/'.join(families)}; GitHub reports {name} is "
                                          f"licensed {spdx}."))
    # One supporting repo is enough; contradictions only stand when no repo matches.
    if any(e["relation"] == Relation.SUPPORTS for e in out):
        out = [e for e in out if e["relation"] != Relation.CONTRADICTS]
    return out


def _named_projects(text: str, own: list[str]) -> list[str]:
    """Project names an open-source claim is about, other than the company and its known repositories/packages
    ("Today we're releasing Spindle, an open-source …" → ['Spindle']). Empty for a claim about the company itself."""
    own_l = {o.lower() for o in own if o}
    for o in sorted(own_l, key=len, reverse=True):  # "Unsloth Desktop", "Modal Sandboxes": the company's own product
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(o)}\s+[A-Z][\w-]*", o, text)
    return [n for n in proper_names(text) if n.lower() not in own_l and not any(n.lower() in o.split() for o in own_l)]


def _repo_named(repo: ArtifactDraft, names: list[str]) -> bool:
    short = str(repo.data.get("full_name") or "").split("/")[-1].lower()
    return any(n.lower() == short or n.lower() in re.split(r"[-_.]", short) for n in names)


def m_open_source(claim: ClaimDraft, ctx: Context) -> list[dict]:
    if not OPEN_SOURCE_RE.search(claim.text):
        return []
    out = []
    repos = ctx.repos()
    named = _named_projects(claim.text, [ctx.facts.name, *ctx.names, *str(ctx.facts.name).split()])
    if named:
        # The claim is about a specific project: only a repository of that name can verify it. Other public repos
        # of the organisation say nothing about whether *this* project is open source (Modal, Nov 6, 2026).
        repos = [r for r in repos if _repo_named(r, named)]
        if not repos:
            return [gap_item(f"gap:repo:{named[0]}", f"No collected public repository is named {', '.join(named)}; "
                             "the organisation's other repositories do not show that it is open source.",
                             "open_source")]
    for repo in repos:
        spdx = repo.data.get("license_spdx")
        name = repo.data.get("full_name")
        if spdx in OSI_LICENSES:
            out.append(evidence_item(repo, relation=Relation.SUPPORTS, strength=Strength.DIRECT, verifies=True,
                                     matcher="open_source", quote=f"public repository, license: {spdx}",
                                     note=f"{name} is a public repository under {spdx} (OSI-approved)."))
        elif spdx in NON_OPEN_LICENSES:
            out.append(evidence_item(repo, relation=Relation.CONTRADICTS, strength=Strength.DIRECT,
                                     matcher="open_source", quote=f"license: {spdx}",
                                     note=f"{name} is under {spdx}, a source-available license that is not "
                                          "OSI-approved open source."))
        else:
            out.append(evidence_item(repo, relation=Relation.CONTEXT, strength=Strength.INDIRECT,
                                     matcher="open_source",
                                     note=f"{name} is public, but GitHub reports no recognised license ({spdx or 'none'})."))
        if repo.data.get("archived"):
            out.append(evidence_item(repo, relation=Relation.CONTEXT, strength=Strength.INDIRECT,
                                     matcher="open_source", note=f"{name} is archived (read-only)."))
        mixed = _nested_licences(ctx.for_repo("tree", name) if name else None)
        if mixed and spdx:
            out.append(evidence_item(
                repo, relation=Relation.CONTEXT, strength=Strength.INDIRECT, matcher="mixed_licence",
                quote=", ".join(p for p, _ in mixed[:3]),
                note=f"{name} is {spdx} at the root, but part of it ships under {', '.join(sorted({lic for _, lic in mixed}))} "
                     f"({mixed[0][0]}): copyleft or source-available terms apply to that component."))
    return out


# Sub-directory licence files whose name states a copyleft or source-available licence (e.g. studio/LICENSE.AGPL-3.0).
_NESTED_LICENCE = re.compile(r"(?:^|/)(?:LICEN[CS]E|COPYING)[.-]((?:A|L)?GPL[-\w.]*|SSPL[-\w.]*|BUSL[-\w.]*|"
                             r"Elastic[-\w.]*|CC-BY-NC[-\w.]*|Commons[-\w.]*)$", re.I)


def _nested_licences(tree: ArtifactDraft | None) -> list[tuple[str, str]]:
    """[(path, licence)] for licence files below the repository root that name a restrictive licence."""
    out = []
    for p in (tree.data.get("paths") or []) if tree else []:
        m = _NESTED_LICENCE.search(p)
        if m and "/" in p and "vendor/" not in p and "node_modules/" not in p:
            out.append((p, m.group(1).upper().replace("LICENSE.", "")))
    return sorted(out)


# Legal boilerplate is never capability evidence, and generic words ("distributed", "parallel") are only meaningful
# as path components, not in prose.
_LEGAL_FILE = re.compile(r"(?:^|/)(?:LICEN[CS]E|NOTICE|COPYING|COPYRIGHT)(?:[.-][^/]*)?$", re.I)
_GENERIC_TEXT_ALIASES = frozenset({"distributed", "parallel", "scale", "charts", "helm", "cluster", "clusters"})


def _file_mentions(art: ArtifactDraft, term: Term) -> str | None:
    if _LEGAL_FILE.search(str(art.data.get("path") or art.url or "")):
        return None
    text = art.text or ""
    m = term.pattern.search(text)
    if not m and term.text_aliases:
        for a in term.path_aliases:
            if len(a) >= 3 and a not in _GENERIC_TEXT_ALIASES:
                m = re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", text, re.I)
                if m:
                    break
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start:end if end != -1 else len(text)].strip()[:200]


CAPABILITY_CATEGORIES = ("hardware", "os", "framework", "model", "scale")


def m_capability(claim: ClaimDraft, ctx: Context) -> list[dict]:
    """Named targets (hardware, OS, framework, model, scale) vs the code tree, manifests, CI and release tags."""
    ctype = claim.claim_type
    if ctype not in (ClaimType.TECHNICAL_CAPABILITY, ClaimType.ARCHITECTURE, ClaimType.PRODUCT,
                     ClaimType.SCALABILITY, ClaimType.PERFORMANCE):
        return []
    baselines = {name for name, _v, _s in baseline_mentions(claim.text)}
    terms = [t for t in find_terms(claim.text, CAPABILITY_CATEGORIES)
             if not (ctype == ClaimType.PERFORMANCE and t.name in baselines)]
    out: list[dict] = []
    for term in terms:
        found: list[dict] = []
        for tree in ctx.kind("tree"):
            paths = [p for p in tree.data.get("paths") or [] if path_matches(p, term.path_aliases)]
            if not paths:
                continue
            src = [p for p in paths if is_source_path(p)]
            shown = (src or paths)[:3]
            found.append(evidence_item(
                tree, relation=Relation.SUPPORTS, strength=Strength.DIRECT if src else Strength.INDIRECT,
                matcher="capability_paths", quote=", ".join(shown),
                note=(f"{len(src)} source file(s) in {tree.data.get('full_name')} reference {term.name}"
                      if src else f"Non-source files in {tree.data.get('full_name')} reference {term.name}"),
            ))
        for f in ctx.kind("file"):
            line = _file_mentions(f, term)
            if line:
                found.append(evidence_item(f, relation=Relation.SUPPORTS, strength=Strength.INDIRECT,
                                           matcher="capability_config", quote=line,
                                           note=f"{f.data.get('path')} references {term.name}."))
        for rel in ctx.kind("releases"):
            tags = [r.get("tag") or "" for r in rel.data.get("releases") or []]
            hit = [t for t in tags if path_matches(t, term.path_aliases) or term.pattern.search(t)]
            if hit:
                found.append(evidence_item(rel, relation=Relation.SUPPORTS, strength=Strength.INDIRECT,
                                           matcher="capability_release", quote=", ".join(hit[:3]),
                                           note=f"Release tags reference {term.name}."))
        if ctype == ClaimType.PERFORMANCE:
            # Code that targets a platform says nothing about a performance figure on it.
            for e in found:
                e.update(relation=Relation.CONTEXT, verifies=False,
                         note=e["note"] + " (the code targets it; the performance figure itself is not evidenced)")
        elif ctype == ClaimType.SCALABILITY:
            for e in found:
                e["strength"] = Strength.INDIRECT
        if not found and ctx.kind("tree"):
            found.append(gap_item(f"gap:{term.name}", f"No public code evidence for {term.name}.", "capability_paths"))
        out.extend(found)
    return out


def m_language(claim: ClaimDraft, ctx: Context) -> list[dict]:
    langs = find_terms(claim.text, ("language",))
    if not langs or not LANGUAGE_CLAIM_RE.search(claim.text):
        return []
    out = []
    for la in ctx.kind("languages"):
        data = {k: v for k, v in (la.data.get("bytes") or {}).items() if isinstance(v, (int, float))}
        total = sum(data.values())
        if not total:
            continue
        repo = la.data.get("full_name")
        for t in langs:
            share = data.get(t.name, 0) / total
            pct = f"{share * 100:.0f}%"
            if share >= 0.25:
                out.append(evidence_item(la, relation=Relation.SUPPORTS, strength=Strength.DIRECT, verifies=True,
                                         matcher="languages", quote=f"{t.name}: {pct} of code bytes",
                                         note=f"GitHub reports {t.name} is {pct} of the code in {repo}."))
            elif share >= 0.02:
                out.append(evidence_item(la, relation=Relation.SUPPORTS, strength=Strength.INDIRECT,
                                         matcher="languages", quote=f"{t.name}: {pct} of code bytes",
                                         note=f"{t.name} is a minority ({pct}) of the code in {repo}."))
            else:
                out.append(evidence_item(la, relation=Relation.CONTRADICTS, strength=Strength.DIRECT,
                                         matcher="languages", quote=f"{t.name}: {pct} of code bytes",
                                         note=f"GitHub reports {t.name} is {pct} of the code in {repo}."))
    return out


def _measure(metric: str, period: str | None, ctx: Context) -> tuple[float | None, ArtifactDraft | None, str]:
    """(value, artifact, description) for a public metric; None when we cannot measure it."""
    metric = metric.lower()
    if metric in ("stars", "forks"):
        repos = ctx.repos()
        if not repos:
            return None, None, ""
        field_name = "stars" if metric == "stars" else "forks"
        total = sum(int(r.data.get(field_name) or 0) for r in repos)
        names = ", ".join(r.data.get("full_name", "") for r in repos)
        return float(total), repos[0], f"{fmt_num(total)} {metric} across {names}"
    if metric == "contributors":
        repos = ctx.repos()
        con = ctx.for_repo("contributors", repos[0].data.get("full_name", "")) if repos else None
        if con is None:
            return None, None, ""
        people = [c for c in con.data.get("contributors") or [] if not str(c.get("login", "")).endswith("[bot]")]
        capped = bool(con.data.get("capped"))
        return float(len(people)), con, (f"at least {len(people)}" if capped else f"{len(people)}") + \
            f" contributors to {con.data.get('full_name')}"
    if metric in ("downloads", "installs"):
        field_name = {"day": "last_day", "week": "last_week", "month": "last_month"}.get(period or "")
        if field_name is None:
            return None, None, ""
        own = [p for p in ctx.kind("package") if "own" in (p.data.get("roles") or [])
               and isinstance((p.data.get("downloads") or {}).get(field_name), (int, float))]
        if not own:
            return None, None, ""
        total = sum(float(p.data["downloads"][field_name]) for p in own)
        names = ", ".join(str(p.data.get("name")) for p in own)
        return total, own[0], f"{fmt_num(total)} downloads in the last {period} ({names})"
    return None, None, ""


def m_traction(claim: ClaimDraft, ctx: Context) -> list[dict]:
    out = []
    for m in TRACTION_METRIC_RE.finditer(claim.text):
        claimed = parse_quantity(m.group(1))
        metric, period = m.group(2).lower(), (m.group(3) or "").lower() or None
        if period is None and re.search(r"\bmonthly\b", m.group(0), re.I):
            period = "month"
        elif period is None and re.search(r"\bweekly\b", m.group(0), re.I):
            period = "week"
        if claimed is None or claimed <= 0:
            continue
        value, art, desc = _measure(metric, period, ctx)
        if art is None or value is None:
            continue
        when = (art.fetched_at.date().isoformat() if art.fetched_at else "collection time")
        capped = "at least" in desc
        if value >= claimed:
            out.append(evidence_item(art, relation=Relation.SUPPORTS, strength=Strength.DIRECT, verifies=True,
                                     matcher="public_metric", quote=desc,
                                     note=f"Measured {desc} on {when}; the company claims {m.group(1).strip()}."))
        elif value < TRACTION_TOLERANCE * claimed and not capped:
            out.append(evidence_item(art, relation=Relation.CONTRADICTS, strength=Strength.DIRECT,
                                     matcher="public_metric", quote=desc,
                                     note=f"Measured {desc} on {when}, below the claimed {m.group(1).strip()} "
                                          f"({value / claimed:.0%} of the claim)."))
        else:
            out.append(evidence_item(art, relation=Relation.SUPPORTS, strength=Strength.INDIRECT,
                                     matcher="public_metric", quote=desc,
                                     note=f"Measured {desc} on {when}, close to the claimed {m.group(1).strip()}."))
    return out


def _latest_release(rel: ArtifactDraft) -> dict | None:
    rs = [r for r in rel.data.get("releases") or [] if not r.get("draft")]
    rs.sort(key=lambda r: (str(r.get("published_at") or ""), str(r.get("tag") or "")), reverse=True)
    return rs[0] if rs else None


def release_maturity(rel: ArtifactDraft) -> tuple[str, str]:
    """('none'|'prerelease'|'pre_1_0'|'stable', description) for a releases artifact."""
    rs = [r for r in rel.data.get("releases") or [] if not r.get("draft")]
    name = rel.data.get("full_name")
    if not rs:
        return "none", f"{name} has no tagged releases"
    stable = [r for r in rs if not r.get("prerelease")]
    latest = _latest_release(rel) or {}
    if not stable:
        return "prerelease", f"{name} has only pre-releases (latest {latest.get('tag')}, {str(latest.get('published_at'))[:10]})"
    stable.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    top = stable[0]
    v = parse_version(str(top.get("tag") or ""))
    if v and v[0] < 1:
        return "pre_1_0", f"{name}'s latest release is {top.get('tag')} ({str(top.get('published_at'))[:10]}), pre-1.0"
    return "stable", f"{name}'s latest release is {top.get('tag')} ({str(top.get('published_at'))[:10]})"


def m_production(claim: ClaimDraft, ctx: Context) -> list[dict]:
    if not PRODUCTION_RE.search(claim.text):
        return []
    out = []
    repos = ctx.repos()
    primary = repos[0].data.get("full_name") if repos else None
    rel = ctx.for_repo("releases", primary) if primary else None
    if rel is not None:
        level, desc = release_maturity(rel)
        if level == "stable":
            out.append(evidence_item(rel, relation=Relation.SUPPORTS, strength=Strength.INDIRECT,
                                     matcher="release_maturity", quote=desc, note=desc + "."))
        else:
            out.append(evidence_item(rel, relation=Relation.CONTRADICTS, strength=Strength.INDIRECT,
                                     matcher="release_maturity", quote=desc,
                                     note=desc + " — claim requires clarification."))
    for pkg in ctx.kind("package"):
        if "own" not in (pkg.data.get("roles") or []):
            continue
        v = parse_version(pkg.data.get("latest_version"))
        if v is None:
            continue
        desc = f"{pkg.data.get('name')} {pkg.data.get('latest_version')} is the latest published version"
        stable = v[0] >= 1 and not v[3]
        out.append(evidence_item(pkg, relation=Relation.SUPPORTS if stable else Relation.CONTRADICTS,
                                 strength=Strength.INDIRECT, matcher="release_maturity", quote=desc,
                                 note=desc + ("." if stable else " (pre-1.0 or pre-release)")))
    return out


def m_maintenance(claim: ClaimDraft, ctx: Context) -> list[dict]:
    if not MAINTAINED_RE.search(claim.text):
        return []
    repos = ctx.repos()
    if not repos:
        return []
    repo = repos[0]
    pushed = parse_dt(repo.data.get("pushed_at"))
    age = days_between(pushed, ctx.as_of)
    if age is None:
        return []
    desc = f"last push to {repo.data.get('full_name')} on {pushed.date().isoformat()} ({int(age)} days before collection)"
    if age <= ACTIVE_PUSH_DAYS:
        return [evidence_item(repo, relation=Relation.SUPPORTS, strength=Strength.DIRECT, verifies=True,
                              matcher="maintenance", quote=desc, note=desc.capitalize() + ".")]
    if age >= STALE_PUSH_DAYS:
        return [evidence_item(repo, relation=Relation.CONTRADICTS, strength=Strength.DIRECT, matcher="maintenance",
                              quote=desc, note=desc.capitalize() + ".")]
    return [evidence_item(repo, relation=Relation.CONTEXT, strength=Strength.INDIRECT, matcher="maintenance",
                          quote=desc, note=desc.capitalize() + ".")]


def cloud_footprint(ctx: Context) -> dict[str, list[dict]]:
    """Cloud provider -> evidence (runtime SDK dependencies and deployment paths)."""
    out: dict[str, list[dict]] = {}
    for svc in (ctx.deps.get("external_services") or []):
        if svc["category"] == "cloud" and svc["runtime"]:
            out.setdefault(svc["vendor"], []).extend(svc["evidence"][:3])
    for tree in ctx.kind("tree"):
        for cloud, aliases in CLOUD_PATH_ALIASES.items():
            hits = [p for p in tree.data.get("paths") or []
                    if path_matches(p, aliases) and re.search(r"deploy|infra|terraform|helm|k8s|\.tf$", p, re.I)]
            if hits:
                out.setdefault(cloud, []).append({"path": hits[0], "item": "deployment path",
                                                  "url": tree.url, "dev": False})
    return dict(sorted(out.items()))


def m_multicloud(claim: ClaimDraft, ctx: Context) -> list[dict]:
    named = find_terms(claim.text, ("cloud",))
    if not (MULTICLOUD_RE.search(claim.text) or len(named) >= 2):
        return []
    clouds = cloud_footprint(ctx)
    anchor = (ctx.kind("tree") or ctx.kind("file") or [None])[0]
    if anchor is None:
        return []
    if len(clouds) >= 2:
        return [evidence_item(anchor, relation=Relation.SUPPORTS, strength=Strength.INDIRECT, matcher="cloud_footprint",
                              quote=", ".join(clouds), note=f"Public code references {', '.join(clouds)}.")]
    if len(clouds) == 1:
        cloud, ev = next(iter(clouds.items()))
        items: dict[str, list[str]] = {}
        for e in ev:
            items.setdefault(e["item"], []).append(e["path"])
        where = f"{cloud} — " + ", ".join(f"{k} ({', '.join(sorted(set(v)))})" for k, v in sorted(items.items())[:3])
        file_art = next((a for a in ctx.kind("file") if any(a.data.get("path") == e["path"] for e in ev)), anchor)
        return [evidence_item(file_art, relation=Relation.CONTRADICTS, strength=Strength.INDIRECT,
                              matcher="cloud_footprint", quote=where,
                              note=f"Public code only references one cloud provider ({where}) — "
                                   "claim requires clarification.")]
    return [evidence_item(anchor, relation=Relation.CONTEXT, strength=Strength.INDIRECT, matcher="cloud_footprint",
                          note="No cloud-provider SDKs or deployment code in the public artifacts.")]


def _key_terms(claim: ClaimDraft) -> list[str]:
    terms = [t.name for t in find_terms(claim.text)]
    terms += [n for n, _v, _s in baseline_mentions(claim.text)]
    terms += re.findall(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?\s?(?:x|×|%)(?![A-Za-z0-9])", claim.text)
    return sorted(set(terms), key=str.lower)


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I) is not None


def m_third_party(claim: ClaimDraft, ctx: Context) -> list[dict]:
    """HN stories/comments and papers that mention the product and one of the claim's key terms."""
    key = _key_terms(claim)
    if not key:
        return []
    out = []
    for art in ctx.third_party():
        text = art.text
        if not any(_mentions(text, n) for n in ctx.names):
            continue
        hits = [k for k in key if _mentions(text, k.replace("×", "x")) or _mentions(text, k)]
        if not hits:
            continue
        neg, pos = NEGATIVE_CUES.search(text), POSITIVE_CUES.search(text)
        if claim.claim_type == ClaimType.PERFORMANCE and not PERF_WORDS.search(text):
            neg = pos = None  # a comment that the product "works" says nothing about a performance figure
        if neg and not pos:
            relation, cue = Relation.CONTRADICTS, neg
        elif pos and not neg:
            relation, cue = Relation.SUPPORTS, pos
        else:
            relation, cue = Relation.CONTEXT, None
        anchor = cue.start() if cue else text.lower().find(hits[0].lower())
        quote = text[max(0, anchor - 120):anchor + 160].strip()
        out.append(evidence_item(
            art, relation=relation, strength=Strength.INDIRECT, matcher="third_party_mention", quote=quote,
            note=f"Independent {art.kind.replace('_', ' ')} mentions {', '.join(hits[:3])}"
                 + (f"; lexical cue '{cue.group(0)}'" if cue else "; no clear positive or negative cue"),
        ))
        if len(out) >= 4:
            break
    return out


def m_benchmark_harness(claim: ClaimDraft, ctx: Context) -> list[dict]:
    if claim.claim_type != ClaimType.PERFORMANCE:
        return []
    out = []
    for tree in ctx.kind("tree"):
        paths = benchmark_paths(tree)
        if paths:
            out.append(evidence_item(tree, relation=Relation.CONTEXT, strength=Strength.INDIRECT,
                                     matcher="benchmark_harness", quote=", ".join(paths[:3]),
                                     note=f"A public benchmark harness exists in {tree.data.get('full_name')} "
                                          "(reproducible in principle; it does not by itself confirm the figure)."))
    return out


def benchmark_paths(tree: ArtifactDraft) -> list[str]:
    return [p for p in tree.data.get("paths") or []
            if re.search(r"(^|/)(bench|benchmarks?|perf)(/|_)|bench(mark)?[\w-]*\.(py|sh|rs|go|cpp|ts|js)$", p, re.I)
            and not p.lower().endswith((".md", ".png", ".jpg", ".svg"))]


MATCHERS = (m_license, m_open_source, m_capability, m_language, m_traction, m_production, m_maintenance,
            m_multicloud, m_third_party, m_benchmark_harness)

_REL_ORDER = {Relation.CONTRADICTS: 0, Relation.SUPPORTS: 1, Relation.CONTEXT: 2}


def sort_evidence(items: list[dict]) -> list[dict]:
    """Conflicts first, then direct support. Ties keep the matchers' order, which visits repositories most-starred
    first, so a claim about the product cites its main repository rather than an alphabetically earlier side repo."""
    order = {id(e): i for i, e in enumerate(items)}
    return sorted(items, key=lambda e: (_REL_ORDER.get(e["relation"], 3), e["strength"] != Strength.DIRECT,
                                        e.get("gap", False), e["publisher"], order[id(e)]))


def attach_evidence(claims: list[ClaimDraft], ctx: Context) -> None:
    """Run every matcher on every claim; deduplicate and cap the evidence list deterministically."""
    for c in claims:
        items: list[dict] = []
        seen: set[tuple[str, str, str, str]] = set()
        for matcher in MATCHERS:
            for e in matcher(c, ctx):
                k = (e["artifact_key"], e["matcher"], e["relation"], e["quote"])
                if k not in seen:
                    seen.add(k)
                    items.append(e)
        c.evidence = sort_evidence(items)[:MAX_EVIDENCE_PER_CLAIM]
