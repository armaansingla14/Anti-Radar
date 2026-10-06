"""Deterministic job-title → technical role class mapping (thesis `hiring_roles`)."""

from __future__ import annotations

import re
from functools import lru_cache

from radar.thesis.loader import Thesis

GTM_CLASS = "gtm"
# Broad classes that only apply when no specialist technical class matches
# ("Embedded Software Engineer" is embedded_firmware, not generic software).
GENERIC_CLASSES = frozenset({"software"})


def _normalize(text: str) -> str:
    """Lower-case and turn punctuation (hyphens, slashes, commas…) into single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", text.lower())).strip()


@lru_cache(maxsize=4096)
def _phrase_re(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(_normalize(phrase)) + r"s?(?![a-z0-9])")


def _tier(role_class: str) -> str:
    if role_class == GTM_CLASS:
        return "gtm"
    return "generic" if role_class in GENERIC_CLASSES else "specialist"


def _best_match(text: str, thesis: Thesis, *, tier: str) -> str | None:
    """Role class of the longest matching phrase within a tier (ties → first class in thesis order)."""
    if not text:
        return None
    best: tuple[int, str] | None = None
    for role_class, phrases in thesis.hiring_roles.items():
        if _tier(role_class) != tier:
            continue
        for phrase in phrases:
            if _phrase_re(phrase).search(text) and (best is None or len(phrase) > best[0]):
                best = (len(phrase), role_class)
    return best[1] if best else None


def classify_role(title: str, department: str | None, thesis: Thesis) -> str | None:
    """Classify a job posting into a thesis role class, or None if unrecognised.

    Specialist technical classes beat generic software, which beats 'gtm'
    (e.g. "Sales Engineer, Embedded" → embedded_firmware). The title is authoritative;
    the department is only consulted when the title has no match in a tier
    (e.g. title "Staff Engineer", department "Firmware").
    """
    t = _normalize(title or "")
    d = _normalize(department or "")
    for tier in ("specialist", "generic", "gtm"):
        found = _best_match(t, thesis, tier=tier) or _best_match(d, thesis, tier=tier)
        if found:
            return found
    return None


def is_technical(role_class: str | None) -> bool:
    return role_class is not None and role_class != GTM_CLASS
