"""The diligence vocabulary: claim types, verification statuses, testability, evidence publishers.

Definitions live here once and are reused by the report, the API (``definitions``) and the method doc, so
the meaning of a status can never drift between what the code decides and what the reader is told.
"""

from __future__ import annotations

from enum import StrEnum


class ClaimType(StrEnum):
    PERFORMANCE = "PERFORMANCE"
    TECHNICAL_CAPABILITY = "TECHNICAL_CAPABILITY"
    ARCHITECTURE = "ARCHITECTURE"
    ECONOMICS = "ECONOMICS"
    PRODUCT = "PRODUCT"
    SECURITY = "SECURITY"
    SCALABILITY = "SCALABILITY"
    CUSTOMER_TRACTION = "CUSTOMER_TRACTION"
    MARKET = "MARKET"
    OPEN_SOURCE = "OPEN_SOURCE"


CLAIM_TYPE_DEFINITIONS: dict[str, str] = {
    ClaimType.PERFORMANCE: "Speed, latency, throughput, memory or quality relative to a baseline.",
    ClaimType.TECHNICAL_CAPABILITY: "What the technology supports or runs on (hardware, frameworks, models, OSes).",
    ClaimType.ARCHITECTURE: "How it is built (language, engine, kernels, the stack it is built on).",
    ClaimType.ECONOMICS: "Cost, price or efficiency in money terms.",
    ClaimType.PRODUCT: "Other concrete product capabilities.",
    ClaimType.SECURITY: "Security, privacy or compliance properties.",
    ClaimType.SCALABILITY: "Behaviour at scale: distributed, multi-GPU / multi-node, large volumes.",
    ClaimType.CUSTOMER_TRACTION: "Users, customers, downloads, stars or other adoption figures.",
    ClaimType.MARKET: "Position relative to the market (first, only, leading, fastest, #1).",
    ClaimType.OPEN_SOURCE: "Open-source status and licensing.",
}


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"
    UNTESTABLE = "UNTESTABLE"


STATUS_ORDER: tuple[str, ...] = tuple(s.value for s in VerificationStatus)

STATUS_DEFINITIONS: dict[str, str] = {
    VerificationStatus.VERIFIED: "Direct independent evidence confirms the claim.",
    VerificationStatus.SUPPORTED: "Multiple credible pieces of evidence support the claim, but full verification "
                                  "is not possible from public evidence.",
    VerificationStatus.PARTIALLY_SUPPORTED: "Some aspects are supported; others are unclear or in question.",
    VerificationStatus.CONTRADICTED: "Credible evidence conflicts with the claim. This is an evidence conflict to "
                                     "resolve with the company, not a finding of deception.",
    VerificationStatus.UNVERIFIED: "Only the company's own statement exists.",
    VerificationStatus.UNTESTABLE: "The claim cannot reasonably be evaluated from public evidence.",
}

# What the UI shows. CONTRADICTED is never displayed as an accusation.
STATUS_LABELS: dict[str, str] = {
    VerificationStatus.VERIFIED: "VERIFIED",
    VerificationStatus.SUPPORTED: "SUPPORTED",
    VerificationStatus.PARTIALLY_SUPPORTED: "PARTIALLY SUPPORTED",
    VerificationStatus.CONTRADICTED: "EVIDENCE CONFLICT",
    VerificationStatus.UNVERIFIED: "UNVERIFIED",
    VerificationStatus.UNTESTABLE: "UNTESTABLE",
}


class Testability(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_TESTABLE = "NOT_TESTABLE"


TESTABILITY_DEFINITIONS: dict[str, str] = {
    Testability.HIGH: "Can be checked or reproduced with public artifacts and modest effort.",
    Testability.MEDIUM: "Testable, but needs setup, hardware, or access the company must describe.",
    Testability.LOW: "Mostly depends on private information; only weak public proxies exist.",
    Testability.NOT_TESTABLE: "Cannot reasonably be evaluated from public evidence.",
}


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Publisher(StrEnum):
    COMPANY = "company"
    CODE = "code"
    REGISTRY = "registry"
    THIRD_PARTY = "third_party"


PUBLISHER_LABELS: dict[str, str] = {
    Publisher.COMPANY: "COMPANY STATEMENT",
    Publisher.CODE: "PUBLIC ARTIFACT",  # the company's own code/releases: verifiable, not an independent opinion
    Publisher.REGISTRY: "REGISTRY METADATA",
    Publisher.THIRD_PARTY: "INDEPENDENT SOURCE",
}

PUBLISHER_DEFINITIONS: dict[str, str] = {
    Publisher.COMPANY: "Written by the company: website pages, README and docs prose, its own launch posts.",
    Publisher.CODE: "The company's public code and releases (source paths, manifests, CI configs, release tags). "
                    "A verifiable artifact, not an independent opinion.",
    Publisher.REGISTRY: "Package registries and vulnerability databases (PyPI, npm, OSV).",
    Publisher.THIRD_PARTY: "Independent authors: Hacker News stories and comments, research papers.",
}

NON_COMPANY = frozenset({Publisher.CODE, Publisher.REGISTRY, Publisher.THIRD_PARTY})


class Relation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class Strength(StrEnum):
    DIRECT = "direct"
    INDIRECT = "indirect"


CONFLICT_LABEL = "EVIDENCE CONFLICT"
CLARIFY_LABEL = "CLAIM REQUIRES CLARIFICATION"

# Underwriting rating scales: evidence strength (higher is better) vs risk (higher is worse).
EVIDENCE_SCALE: tuple[str, ...] = ("STRONG", "MEDIUM", "WEAK", "UNKNOWN")
RISK_SCALE: tuple[str, ...] = ("LOW", "MODERATE", "HIGH", "UNKNOWN")


def definitions() -> dict[str, dict[str, str]]:
    """Every definition, for the API and the report appendix."""
    return {
        "claim_types": {str(k): v for k, v in CLAIM_TYPE_DEFINITIONS.items()},
        "statuses": {str(k): v for k, v in STATUS_DEFINITIONS.items()},
        "status_labels": {str(k): v for k, v in STATUS_LABELS.items()},
        "testability": {str(k): v for k, v in TESTABILITY_DEFINITIONS.items()},
        "publishers": {str(k): v for k, v in PUBLISHER_DEFINITIONS.items()},
        "publisher_labels": {str(k): v for k, v in PUBLISHER_LABELS.items()},
    }
