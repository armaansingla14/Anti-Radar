"""Detect founding-activity language (thesis `formation_phrases`) in free text."""

from __future__ import annotations

import re
from functools import lru_cache

from radar.thesis.loader import Thesis

# Thesis phrases that alone are too common to indicate formation in some contexts:
# GENERIC — routine company marketing/hiring copy; PERSONAL — only meaningful in a person's own bio/post.
GENERIC_PHRASES = frozenset({"we're hiring", "launching"})
PERSONAL_PHRASES = frozenset({"co-founder", "cofounder", "just left", "day 1 at"})

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'"})
_CONTRACTIONS = {"'re": r"(?:'re|\s+are)", "'ve": r"(?:'ve|\s+have)", "'m": r"(?:'m|\s+am)"}


def normalize_text(text: str) -> str:
    """Lower-case, straighten curly apostrophes and collapse whitespace."""
    return re.sub(r"\s+", " ", text.translate(_APOSTROPHES).lower())


@lru_cache(maxsize=512)
def _phrase_re(phrase: str) -> re.Pattern[str]:
    """Word-bounded pattern; tolerates expanded contractions, flexible spacing/hyphens, plural 's'."""
    p = normalize_text(phrase).strip()
    out = ""
    i = 0
    while i < len(p):
        for short, alt in _CONTRACTIONS.items():
            if p.startswith(short, i) and (i + len(short) == len(p) or not p[i + len(short)].isalnum()):
                out += alt
                i += len(short)
                break
        else:
            ch = p[i]
            out += r"[\s\-]+" if ch == " " else re.escape(ch)
            i += 1
    return re.compile(r"(?<![a-z0-9])" + out + r"s?(?![a-z0-9])")


def find_formation_matches(text: str | None, thesis: Thesis) -> list[tuple[str, int, int]]:
    """(phrase, start, end) for each thesis phrase found (first occurrence), in thesis order.

    Offsets refer to `normalize_text(text)`, which preserves the input length except for
    collapsed whitespace runs.
    """
    if not text:
        return []
    norm = normalize_text(text)
    out: list[tuple[str, int, int]] = []
    for phrase in thesis.formation_phrases:
        m = _phrase_re(phrase).search(norm)
        if m:
            out.append((phrase, m.start(), m.end()))
    return out


def detect_formation_phrases(text: str | None, thesis: Thesis) -> list[str]:
    """Thesis formation phrases present in `text` (case-insensitive, word-bounded)."""
    return [p for p, _, _ in find_formation_matches(text, thesis)]


def phrase_context(text: str, start: int, end: int, width: int = 90) -> str:
    """Short verbatim quote around a match in normalized text, for provenance."""
    norm = normalize_text(text)
    s, e = max(0, start - width), min(len(norm), end + width)
    return ("…" if s > 0 else "") + norm[s:e].strip() + ("…" if e < len(norm) else "")
