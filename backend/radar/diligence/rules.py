"""Deterministic verification-status rules and testability classification.

A status is never chosen by a model. ``decide_status`` applies these ordered rules to the evidence list;
the first that matches wins and its id is written into the reason string (and documented verbatim in
``docs/DILIGENCE_METHOD.md``):

R1  Any direct contradiction from code, registry or a third party → CONTRADICTED (shown as EVIDENCE CONFLICT).
R2  Any direct evidence that verifies the claim, from code, registry or a third party, with no contradiction
    of any kind and no uncovered aspect → VERIFIED. (A pending clarification keeps a claim out of VERIFIED: a
    verified license does not settle "fully open source" when the company also describes closed components.)
R3  At least two supporting items from non-company publishers, on distinct artifacts, with no contradiction of
    any kind and no uncovered aspect → SUPPORTED.
R4  At least one non-company supporting item, but only one, or mixed with indirect conflicts (from any
    publisher, including the company's own conflicting statements) or with aspects that have no evidence
    → PARTIALLY_SUPPORTED.
R5  Testability NOT_TESTABLE and no non-company support → UNTESTABLE.
R6  Otherwise → UNVERIFIED (only the company's own statement exists).

Company statements (``publisher == company``) never count as support; ``context`` items never count either way.
"""

from __future__ import annotations

import re
from typing import Any

from radar.diligence.taxonomy import ClaimType, Confidence, Publisher, Relation, Strength, Testability
from radar.diligence.taxonomy import VerificationStatus as VS
from radar.diligence.types import ClaimDraft
from radar.diligence.vocab import find_terms

RULES: dict[str, str] = {
    "R1": "Any direct contradiction from code, registry or a third party → CONTRADICTED (EVIDENCE CONFLICT).",
    "R2": "Any direct verifying evidence from code, registry or a third party, with no contradiction of any kind "
          "and no uncovered aspect → VERIFIED.",
    "R3": "≥2 supporting items from non-company publishers on distinct artifacts, with no contradiction of any "
          "kind and no uncovered aspect → SUPPORTED.",
    "R4": "≥1 non-company supporting item, but only one, or mixed with indirect conflicts or uncovered aspects "
          "→ PARTIALLY_SUPPORTED.",
    "R5": "Testability NOT_TESTABLE and no non-company support → UNTESTABLE.",
    "R6": "Otherwise → UNVERIFIED (only the company's own statement exists).",
}


def _pub(e: dict[str, Any]) -> str:
    return str(e.get("publisher"))


def decide_status(claim: ClaimDraft | None, evidence: list[dict[str, Any]], testability: str
                  ) -> tuple[str, str, str]:
    """(status, reason, confidence). ``claim`` is unused by the rules (statuses depend on evidence only); it is
    accepted so callers can pass it for future rule auditing."""
    ext = [e for e in evidence if _pub(e) != Publisher.COMPANY and not e.get("gap")]
    direct_contra = [e for e in ext if e["relation"] == Relation.CONTRADICTS and e["strength"] == Strength.DIRECT]
    indirect_contra = [e for e in evidence if e["relation"] == Relation.CONTRADICTS and e["strength"] != Strength.DIRECT
                       or (e["relation"] == Relation.CONTRADICTS and _pub(e) == Publisher.COMPANY)]
    supports = [e for e in ext if e["relation"] == Relation.SUPPORTS]
    verifies = [e for e in supports if e["strength"] == Strength.DIRECT and e.get("verifies")]
    gaps = [e for e in evidence if e.get("gap")]
    pubs = lambda items: "/".join(sorted({_pub(e) for e in items}))  # noqa: E731

    if direct_contra:
        conf = Confidence.HIGH if any(_pub(e) in (Publisher.CODE, Publisher.REGISTRY) for e in direct_contra) \
            else Confidence.MEDIUM
        return VS.CONTRADICTED, f"R1: direct conflicting evidence from {pubs(direct_contra)} — " \
                                f"{direct_contra[0]['note']}", conf
    if verifies and not gaps and not indirect_contra:
        return VS.VERIFIED, f"R2: verified by {pubs(verifies)} — {verifies[0]['note']}", Confidence.HIGH
    distinct = {e["artifact_key"] for e in supports}
    if len(distinct) >= 2 and not indirect_contra and not gaps:
        conf = Confidence.HIGH if any(e["strength"] == Strength.DIRECT for e in supports) else Confidence.MEDIUM
        return VS.SUPPORTED, f"R3: {len(distinct)} supporting artifacts from {pubs(supports)}; no conflicting " \
                             "evidence", conf
    if supports:
        why = []
        if len(distinct) == 1:
            why.append("one supporting artifact")
        if indirect_contra:
            why.append(f"{len(indirect_contra)} item(s) require clarification")
        if gaps:
            why.append("no evidence for: " + ", ".join(sorted({e['note'].removeprefix('No public code evidence for ')
                                                               .rstrip('.') for e in gaps})))
        conf = Confidence.MEDIUM if any(e["strength"] == Strength.DIRECT for e in supports) else Confidence.LOW
        return VS.PARTIALLY_SUPPORTED, "R4: partial support — " + "; ".join(why or ["mixed evidence"]), conf
    if testability == Testability.NOT_TESTABLE:
        return VS.UNTESTABLE, "R5: cannot reasonably be evaluated from public evidence", Confidence.LOW
    tail = f"; {len(indirect_contra)} item(s) require clarification" if indirect_contra else ""
    return VS.UNVERIFIED, f"R6: only the company's own statement{tail}", Confidence.LOW


def rule_id(reason: str) -> str:
    m = re.match(r"(R\d)", reason or "")
    return m.group(1) if m else ""


# ── testability ──────────────────────────────────────────────────────────────

_NUMBER = re.compile(r"\d")
_PUBLIC_METRIC = re.compile(r"\b(?:stars|downloads|installs|contributors|forks)\b", re.I)
_PRIVATE_METRIC = re.compile(r"\b(?:users|customers|companies|teams|developers|organi[sz]ations|enterprises"
                             r"|businesses|startups|deployments|revenue|ARR|MRR|Fortune)\b", re.I)
_ROADMAP = re.compile(r"\b(?:coming soon|roadmap|will (?:soon )?(?:support|add|ship|launch)|planned|in the future)\b",
                      re.I)
_COMPLIANCE = re.compile(r"\bSOC ?2\b|\bHIPAA\b|\bISO ?27001\b|\bGDPR\b|\bcertified\b|\bcompliant\b", re.I)
_SUPERLATIVE = re.compile(r"\b(?:fastest|leading|first|only|#1|number one|largest|best|most|world'?s)\b", re.I)
_WORD_MULTIPLIER = re.compile(r"\b(?:double[sd]?|twice|triple[sd]?|halve[sd]?)\b", re.I)
_BASELINE_NAMED = re.compile(r"\b(?:than|vs\.?|versus|against|compared to|over)\s+[A-Z]", re.I)


def classify_testability(claim: ClaimDraft) -> str:
    """Deterministic: what it would take to test this claim with public means."""
    text, t = claim.text, claim.claim_type
    if _ROADMAP.search(text):
        return Testability.NOT_TESTABLE
    has_num = bool(_NUMBER.search(text))
    if t == ClaimType.PERFORMANCE:
        if has_num or _WORD_MULTIPLIER.search(text):  # "double throughput" states a figure (2×)
            return Testability.HIGH
        return Testability.MEDIUM if _BASELINE_NAMED.search(text) else Testability.LOW
    if t == ClaimType.TECHNICAL_CAPABILITY:
        return Testability.HIGH if find_terms(text) else Testability.MEDIUM
    if t == ClaimType.OPEN_SOURCE:
        return Testability.HIGH
    if t == ClaimType.CUSTOMER_TRACTION:
        if _PUBLIC_METRIC.search(text):
            return Testability.HIGH
        if re.search(r"\b(?:used|trusted|relied on|adopted|deployed) by\b|\bin production at\b", text, re.I) \
                and not has_num:
            return Testability.LOW  # named references can sometimes be checked publicly
        return Testability.NOT_TESTABLE
    if t == ClaimType.ARCHITECTURE:
        return Testability.HIGH if find_terms(text, ("language",)) and re.search(r"written|implemented", text, re.I) \
            else Testability.MEDIUM
    if t == ClaimType.SECURITY:
        return Testability.NOT_TESTABLE if _COMPLIANCE.search(text) else Testability.MEDIUM
    if t in (ClaimType.SCALABILITY, ClaimType.ECONOMICS):
        return Testability.MEDIUM if has_num else Testability.LOW
    if t == ClaimType.MARKET:
        return Testability.NOT_TESTABLE if _SUPERLATIVE.search(text) else Testability.LOW
    if t == ClaimType.PRODUCT:
        return Testability.LOW if _PRIVATE_METRIC.search(text) else Testability.MEDIUM
    return Testability.LOW


def apply_rules(claims: list[ClaimDraft]) -> None:
    for c in claims:
        c.testability = classify_testability(c)
        c.status, c.status_reason, c.confidence = (str(x) for x in decide_status(c, c.evidence, c.testability))
        c.rule = rule_id(c.status_reason)
