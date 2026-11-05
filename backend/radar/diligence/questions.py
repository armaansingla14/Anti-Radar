"""Questions for founders, derived only from evidence gaps and conflicts.

Every question names the specific claim, artifact or measurement that prompted it and carries the reason.
There are no generic templates ("What is your moat?"): if the evidence raises nothing, there is no question.
Ordered by materiality: conflicts, material security findings, high-severity dependency vulnerabilities,
untested performance figures, maintainer concentration, traction, deprecated dependencies, AI-system gaps.
"""

from __future__ import annotations

import re
from typing import Any

from radar.diligence.taxonomy import ClaimType, Testability
from radar.diligence.taxonomy import VerificationStatus as VS
from radar.diligence.types import ClaimDraft

MAX_QUESTIONS = 10


def _q(question: str, reason: str, priority: int, ordinals: list[int] | None = None,
       evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    refs = [{"url": e.get("url", ""), "title": e.get("title", ""), "artifact_key": e.get("artifact_key", "")}
            for e in (evidence or []) if e.get("url") or e.get("artifact_key")]
    return {"question": question, "reason": reason, "claim_ordinals": sorted(set(ordinals or [])),
            "evidence": refs[:3], "priority": priority}


def _short(text: str, n: int = 140) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _from_conflict(c: dict[str, Any], claims: dict[int, ClaimDraft], product: str) -> dict[str, Any] | None:
    d, ev = c.get("details") or {}, c["evidence"]
    ords = c["claim_ordinals"]
    claim = claims.get(ords[0]) if ords else None
    if c["kind"] == "stale_benchmark_baseline":
        mult = ""
        if claim is not None:
            m = re.search(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?\s?(?:x|×|%)(?![A-Za-z0-9])", claim.text)
            mult = f" {m.group(0)}" if m else ""
        when = f" on {d['latest_date']}" if d.get("latest_date") else ""
        return _q(f"Your benchmark shows{mult} against {d['baseline']} {d['claimed_version']}; {d['baseline']} "
                  f"{d['latest_version']} shipped{when}. Have you rerun it, and what changed?",
                  c["summary"], 100, ords, ev)
    if c["kind"] == "production_ready_no_stable_release":
        return _q(f"You describe {product} as production ready, but {d.get('releases') or 'there is no stable release'}. "
                  "What version do production users run, and what stability commitment do you make?",
                  c["summary"], 90, ords, ev)
    if c["kind"] == "multi_cloud_single_provider":
        return _q(f"You claim multi-cloud deployment, but the public code only references {ev[0]['quote'] if ev else 'one provider'}. "
                  "Which other clouds run in production today, and where is that code?", c["summary"], 85, ords, ev)
    if c["kind"] == "open_source_incomplete":
        quote = _short(ev[0]["quote"], 120) if ev else "company pages mention proprietary components"
        return _q(f"You say {product} is fully open source, but your own page says “{quote}”. Which components are "
                  "proprietary or cloud-only, and are they needed in production?", c["summary"], 88, ords, ev)
    if c["kind"] == "license_mismatch":
        return _q(f"Your materials describe the license as open source, but {ev[0]['note'] if ev else c['summary']} "
                  "Which license governs commercial use?", c["summary"], 95, ords, ev)
    if claim is not None:
        note = ev[0]["note"] if ev else c["summary"]
        return _q(f"You state “{_short(claim.text)}”, but {note[:1].lower() + note[1:]} "
                  "Which figure is current, and how is it measured?", c["summary"], 96, ords, ev)
    return None


def generate_questions(claims: list[ClaimDraft], conflicts: list[dict[str, Any]], *, oss: dict[str, Any],
                       deps: dict[str, Any], sec: dict[str, Any], ai: dict[str, Any] | None,
                       product: str) -> list[dict[str, Any]]:
    by_ord = {c.ordinal: c for c in claims}
    out: list[dict[str, Any]] = []
    covered: set[int] = set()

    for c in conflicts:
        q = _from_conflict(c, by_ord, product)
        if q:
            out.append(q)
            covered.update(c["claim_ordinals"])

    for f in sec.get("findings") or []:
        if not f["material_question"]:
            continue
        e = f["evidence"]
        if f["kind"] == "secret":
            loc = f"{e['path']} line {e['line']}" if e.get("line") else e["path"]
            out.append(_q(f"A pattern resembling a credential appears in {loc} of the public repository. Is it a "
                          "live credential, and has it been revoked and rotated?", f["title"], 92, [],
                          [{"url": e["url"], "title": e["path"], "artifact_key": e.get("artifact_key", "")}]))
        elif f["kind"] == "advisory":
            out.append(_q(f"{f['title'].split(':')[0]} was published for your code. Which release fixes it, and how "
                          "were affected users notified?", f["title"], 80, [],
                          [{"url": e["url"], "title": f["title"], "artifact_key": e.get("artifact_key", "")}]))

    for c in claims:
        if c.ordinal in covered:
            continue
        if c.claim_type == ClaimType.PERFORMANCE and c.testability == Testability.HIGH \
                and c.status in (VS.UNVERIFIED, VS.PARTIALLY_SUPPORTED):
            out.append(_q(f"You claim “{_short(c.source_quote)}”. Can you share the exact benchmark configuration — "
                          "hardware, model and precision, batch and sequence lengths, baseline version and commands — "
                          "so it can be reproduced?",
                          f"Claim {c.ordinal} is {c.status.replace('_', ' ').lower()}: no independent reproduction "
                          "was found.", 70, [c.ordinal], c.company_evidence[:1]))
        elif c.claim_type == ClaimType.CUSTOMER_TRACTION and c.status in (VS.UNVERIFIED, VS.UNTESTABLE):
            out.append(_q(f"You state “{_short(c.source_quote)}”. Which named references or usage data can be "
                          "checked independently?",
                          f"Claim {c.ordinal} relies only on the company's statement.", 60, [c.ordinal],
                          c.company_evidence[:1]))

    for r in oss.get("risks") or []:
        if r["id"] == "single_maintainer":
            out.append(_q(f"{r['title']} in {oss.get('repo')}. Who else can review, merge and release core changes "
                          "if that person is unavailable?", "Single-maintainer concentration (≥60% of commits).", 65,
                          [], [{"url": r["url"], "title": r["title"], "artifact_key": ""}]))
        elif r["id"] == "no_recent_commits":
            out.append(_q(f"{oss.get('repo')} shows no commits in the last 12 weeks. Where does development happen "
                          "now?", r["title"], 62, [], [{"url": r["url"], "title": r["title"], "artifact_key": ""}]))

    for r in deps.get("risks") or []:
        if r["id"] == "vulnerability" and r["severity"] == "HIGH":
            out.append(_q(f"{r['title']}. Is that pinned version used in production, and when will it be upgraded?",
                          r["detail"] or r["title"], 75, [], [{"url": r["url"], "title": r["title"],
                                                               "artifact_key": r.get("artifact_key", "")}]))
        elif r["id"] == "deprecated" and not r.get("dev"):
            out.append(_q(f"{r['title']}. What is the replacement plan?", r["detail"] or r["title"], 55, [],
                          [{"url": r["url"], "title": r["title"], "artifact_key": r.get("artifact_key", "")}]))

    if ai is not None and len(ai.get("llm_providers") or []) == 1 and not ai.get("routers"):
        vendor = ai["llm_providers"][0]
        out.append(_q(f"All hosted model calls in the public code go through {vendor}. What happens to the product if "
                      f"{vendor} changes pricing, rate limits or model behaviour, and have you tested another provider?",
                      "Single model-provider dependency with no switching evidence.", 50))
    if ai is not None and not ai.get("eval_evidence"):
        out.append(_q("No evaluation harness is visible in the public code. How do you measure model quality and catch "
                      "regressions before release?", "No public evaluation evidence (AI systems review).", 40))

    seen: set[str] = set()
    uniq = []
    for q in sorted(out, key=lambda q: (-q["priority"], q["claim_ordinals"][:1] or [999], q["question"])):
        if q["question"] not in seen:
            seen.add(q["question"])
            uniq.append(q)
    return uniq[:MAX_QUESTIONS]
