"""Lightweight public-artifact security review.

Only text we already fetched is inspected: manifests, example configs, Dockerfiles, README/docs prose and
published advisories. Nothing is exploited, scanned or probed. Possible secrets are reported with the value
masked (never more than the first four characters) and the wording "possible secret pattern … — verify".
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from radar.diligence.deps import manifest_kind
from radar.diligence.taxonomy import Publisher
from radar.diligence.types import ArtifactDraft

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

SECRET_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("AWS access key id", "HIGH", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("private key", "HIGH", re.compile(r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----)")),
    ("API secret key (sk-…)", "HIGH", re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,})\b")),
    ("GitHub token", "HIGH", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b")),
    ("Slack token", "HIGH", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("Google API key", "HIGH", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b")),
)
GENERIC_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|passwd|password)[A-Z0-9_]*)\s*[:=]\s*[\"']?([A-Za-z0-9/+_=.-]{16,})")
PLACEHOLDER = re.compile(r"(?i)your|example|xxxx|changeme|placeholder|dummy|redacted|<|\$\{|replace|insert|here\b")

DEFAULT_PASSWORD = re.compile(
    r"(?i)^\s*-?\s*([A-Z0-9_]*(?:PASSWORD|PASSWD|PASS|PWD|SECRET)[A-Z0-9_]*)\s*[:=]\s*[\"']?"
    r"(changeme|password|admin|root|secret|123456|postgres|default|pass)[\"']?\s*$")
DEBUG_TRUE = re.compile(r"(?i)^\s*-?\s*[A-Z_]*DEBUG\s*[:=]\s*[\"']?(true|1|yes|on)[\"']?\s*$")
BIND_ALL = re.compile(r"(?i)(?:--host[= ]|\bHOST\s*[:=]\s*[\"']?|\bbind\s*[:=]?\s*[\"']?)0\.0\.0\.0")
AUTH_HINT = re.compile(r"(?i)auth|api[_-]?key|token")  # a database password says nothing about the service
CHMOD_777 = re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b")
CURL_PIPE = re.compile(r"(?i)\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")
TRUST_REMOTE = re.compile(r"trust_remote_code\s*=\s*True")
TORCH_LOAD = re.compile(r"torch\.load\((?![^)\n]*weights_only\s*=\s*True)[^)\n]*\)")
PICKLE_LOAD = re.compile(r"\bpickle\.loads?\(")


def mask_secret(value: str) -> str:
    """Never reveal more than the first four characters."""
    v = value or ""
    return (v[:4] if len(v) > 8 else "") + "…[masked]"


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _path(art: ArtifactDraft) -> str:
    return str((art.data or {}).get("path") or art.title or art.key)


def _finding(severity: str, title: str, art: ArtifactDraft, quote: str, why: str, material: bool,
             line: int | None = None, kind: str = "") -> dict[str, Any]:
    return {"id": "", "kind": kind, "severity": severity, "title": title,
            "evidence": {"path": _path(art), "url": art.url, "line": line, "quote": quote[:240],
                         "artifact_key": art.key},
            "why_it_matters": why, "material_question": material}


def _scan_secrets(art: ArtifactDraft) -> list[dict[str, Any]]:
    out = []
    for n, line in enumerate((art.text or "").splitlines(), 1):
        hit = False
        for label, sev, pat in SECRET_PATTERNS:
            m = pat.search(line)
            if m and not PLACEHOLDER.search(m.group(1)):
                masked = line.replace(m.group(1), mask_secret(m.group(1))).strip()
                out.append(_finding(sev, f"Possible secret pattern ({label}) in {_path(art)} line {n} — verify",
                                    art, masked, "A live credential in a public repository can be used by anyone; "
                                    "it must be revoked and rotated, not just deleted.", True, n, "secret"))
                hit = True
                break
        if hit:
            continue
        g = GENERIC_SECRET.search(line)
        if g and not PLACEHOLDER.search(g.group(2)) and entropy(g.group(2)) >= 3.5:
            masked = line.replace(g.group(2), mask_secret(g.group(2))).strip()
            out.append(_finding("MEDIUM", f"Possible secret pattern ({g.group(1)}) in {_path(art)} line {n} — verify",
                                art, masked, "A high-entropy value assigned to a secret-named variable may be a real "
                                "credential.", True, n, "secret"))
    return out


def _scan_config(art: ArtifactDraft) -> list[dict[str, Any]]:
    out = []
    text = art.text or ""
    has_auth = bool(AUTH_HINT.search(text))
    for n, line in enumerate(text.splitlines(), 1):
        if DEFAULT_PASSWORD.search(line):
            out.append(_finding("MEDIUM", f"Default password in example configuration ({_path(art)} line {n})", art,
                                re.sub(r"[:=].*", "= …", line.strip()),
                                "Example configs are copied into deployments; a default password often survives "
                                "into production.", False, n, "insecure_default"))
        elif DEBUG_TRUE.search(line):
            out.append(_finding("LOW", f"Debug mode enabled in example configuration ({_path(art)} line {n})", art,
                                line.strip(), "Debug modes can expose stack traces and internals.", False, n,
                                "insecure_default"))
        if BIND_ALL.search(line) and not has_auth:
            out.append(_finding("LOW", f"Binds to 0.0.0.0 with no visible authentication ({_path(art)} line {n})", art,
                                line.strip(), "A service on all interfaces without auth is reachable by anyone on "
                                "the network.", False, n, "insecure_default"))
        if CHMOD_777.search(line):
            out.append(_finding("MEDIUM", f"World-writable permissions (chmod 777) in {_path(art)} line {n}", art,
                                line.strip(), "Any local user or process can modify these files.", False, n,
                                "insecure_default"))
    return out


def _scan_supply_chain(art: ArtifactDraft) -> list[dict[str, Any]]:
    out = []
    for n, line in enumerate((art.text or "").splitlines(), 1):
        if CURL_PIPE.search(line):
            out.append(_finding("MEDIUM", f"Install instructions pipe a remote script into a shell ({_path(art)})",
                                art, line.strip(), "Whoever controls that URL (or a network attacker) controls what "
                                "runs on the user's machine.", False, n, "supply_chain"))
        if TRUST_REMOTE.search(line):
            out.append(_finding("MEDIUM", f"trust_remote_code=True in {_path(art)}", art, line.strip(),
                                "Loading a model with trust_remote_code executes Python shipped with the model.",
                                False, n, "supply_chain"))
        if TORCH_LOAD.search(line):
            out.append(_finding("MEDIUM", f"torch.load without weights_only=True in {_path(art)}", art, line.strip(),
                                "torch.load unpickles arbitrary objects; a malicious checkpoint can execute code.",
                                False, n, "supply_chain"))
        if PICKLE_LOAD.search(line):
            out.append(_finding("LOW", f"pickle.load in {_path(art)} — verify the input is trusted", art, line.strip(),
                                "Unpickling untrusted data executes code.", False, n, "supply_chain"))
    return out


def _scan_dockerfile(art: ArtifactDraft) -> list[dict[str, Any]]:
    from radar.diligence.deps import parse_dockerfile

    d = parse_dockerfile(art.text or "", _path(art))
    out = []
    if d["runs_as_root"] and d["from"]:
        out.append(_finding("MEDIUM", f"Container runs as root ({_path(art)} has no non-root USER)", art,
                            "no USER directive" if d["user"] is None else f"USER {d['user']}",
                            "A compromise of the process is a compromise of the container's root account.",
                            False, None, "dockerfile"))
    for f in d["from"]:
        if f["latest"] and not f["pinned_digest"] and f["image"].lower() != "scratch":
            out.append(_finding("LOW", f"Unpinned base image {f['image']} ({_path(art)} line {f['line']})", art,
                                f"FROM {f['image']}", "Builds silently change when the :latest tag moves.", False,
                                f["line"], "dockerfile"))
    for a in d["add_urls"]:
        out.append(_finding("MEDIUM", f"ADD from a URL in {_path(art)} line {a['line']}", art, a["quote"],
                            "Remote content is pulled at build time without integrity checks.", False, a["line"],
                            "dockerfile"))
    return out


def security_review(artifacts: list[ArtifactDraft], deps: dict[str, Any] | None = None) -> dict[str, Any]:
    files = sorted([a for a in artifacts if a.kind == "file"], key=lambda a: a.key)
    prose = sorted([a for a in artifacts if a.publisher == Publisher.COMPANY and a.kind in ("readme", "doc", "web_page")],
                   key=lambda a: a.key)
    trees = [a for a in artifacts if a.kind == "tree"]
    findings: list[dict[str, Any]] = []

    for f in files:
        findings += _scan_secrets(f)
        kind = manifest_kind(_path(f))
        if kind in ("env_example", "compose") or re.search(r"\.(ya?ml|toml|ini|cfg|conf|sh)$|config", _path(f), re.I):
            findings += _scan_config(f)
        if kind == "dockerfile":
            findings += _scan_dockerfile(f)
            findings += [x for x in _scan_config(f) if "chmod" in x["title"]]
    for a in prose + [f for f in files if f.kind == "file" and manifest_kind(_path(f)) is None]:
        findings += _scan_supply_chain(a)

    advisories = []
    for adv in sorted([a for a in artifacts if a.kind == "advisories"], key=lambda a: a.key):
        for item in adv.data.get("advisories") or []:
            sev = str(item.get("severity") or "").upper()
            level = "HIGH" if sev in ("HIGH", "CRITICAL") else "MEDIUM" if sev in ("MEDIUM", "MODERATE") else "LOW"
            ident = item.get("ghsa_id") or item.get("cve_id") or "advisory"
            advisories.append(item)
            findings.append({
                "id": "", "kind": "advisory", "severity": level,
                "title": f"Published security advisory {ident} ({sev.lower() or 'unrated'}): "
                         f"{str(item.get('summary') or '')[:120]}",
                "evidence": {"path": adv.data.get("full_name"), "url": item.get("html_url") or adv.url, "line": None,
                             "quote": str(item.get("summary") or "")[:240], "artifact_key": adv.key},
                "why_it_matters": "A disclosed vulnerability in the company's own code; check the fixed version and "
                                  "how users were notified.",
                "material_question": level == "HIGH",
            })

    has_security_md: bool | None = None
    if trees:
        paths = {p.lower() for t in trees for p in t.data.get("paths") or []}
        has_security_md = any(p in paths for p in ("security.md", ".github/security.md", "docs/security.md"))
        if not has_security_md:
            # Cite the main (most-starred) repository, not an alphabetically earlier side repo.
            stars = {a.data.get("full_name"): a.data.get("stars") or 0 for a in artifacts if a.kind == "repo"}
            t = sorted(trees, key=lambda a: (-stars.get(a.data.get("full_name"), 0), a.key))[0]
            findings.append(_finding("LOW", "No SECURITY.md (vulnerability disclosure policy) in the repository", t,
                                     "no SECURITY.md at the root, .github/ or docs/",
                                     "Without a disclosure policy, researchers have no clear channel to report issues.",
                                     False, None, "policy"))

    # Deduplicate (same title + path + line; the same piped install command once, wherever it is repeated) and order
    # deterministically.
    uniq: dict[tuple, dict[str, Any]] = {}
    for f in findings:
        url = re.search(r"https?://\S+", f["evidence"].get("quote") or "") if f["kind"] == "supply_chain" else None
        if url and "pipe a remote script" in f["title"]:
            key: tuple = ("curl_pipe", url.group(0).rstrip("`'\")"))
        else:
            key = (f["title"], f["evidence"]["path"], f["evidence"]["line"])
        uniq.setdefault(key, f)
    findings = sorted(uniq.values(), key=lambda f: (SEVERITY_ORDER[f["severity"]], f["kind"], f["title"],
                                                    f["evidence"]["line"] or 0))
    for i, f in enumerate(findings, 1):
        f["id"] = f"SEC-{i}"
    return {
        "findings": findings,
        "posture": {
            "security_md": has_security_md,
            "published_advisories": len(advisories) if any(a.kind == "advisories" for a in artifacts) else None,
            "files_scanned": [_path(f) for f in files],
            "scope": "Public artifacts only: fetched manifests, example configs, Dockerfiles, README/docs text and "
                     "published advisories. No live system was scanned or probed.",
        },
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in ("HIGH", "MEDIUM", "LOW")},
        "material_questions": sum(1 for f in findings if f["material_question"]),
        "reviewed": bool(files or trees),
    }
