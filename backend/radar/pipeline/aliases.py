"""Organization-name normalization for free-text affiliations.

Self-reported strings ("@google-deepmind", "Google DeepMind, London", "FAIR") are mapped
to one canonical name via ``config/org_aliases.yaml`` so that comparisons ("did this person
change employer?", "do these two people share a prior employer?") do not depend on spelling.

Rules (conservative, deterministic):

1. the whole string, cleaned (no leading "@", punctuation, legal suffix), is an alias -> canonical
2. one segment of it (split on , ; / | ( ) · and multiple @handles) is an alias -> canonical
3. a *distinctive* alias (two or more words, or one word of 6+ characters) appears as a whole
   phrase inside the string -> canonical (longest alias wins). Short or ordinary-word aliases
   ("meta", "fair", "tri", "hf") only ever match rule 1/2.
4. otherwise the cleaned original string is returned unchanged.

Strings with no letters or digits (emoji, "✖️", "-") normalize to None.
The raw value is always kept by callers; this never rewrites what a person wrote.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from radar.settings import ROOT, get_settings

_LEGAL = {"inc", "llc", "ltd", "corp", "co", "corporation", "incorporated", "limited", "gmbh", "pbc", "plc"}
_SEGMENT_SPLIT = re.compile(r"[,;/|()\[\]·•–—]+|\s+@|\s+-\s+|\s+&\s+|\s+and\s+|\s+at\s+")
DISTINCTIVE_MIN_LEN = 6


def org_key(name: str | None) -> str:
    """Lower-case, accent-free, punctuation-free key with legal suffixes removed."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\\", " ").replace("{", " ").replace("}", " ")  # BibTeX escapes
    tokens = re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL:
        tokens.pop()
    return " ".join(tokens)


def clean_org(name: str | None) -> str | None:
    """Whitespace-collapsed string without a leading '@'; None when nothing meaningful is left."""
    if not name:
        return None
    n = re.sub(r"\s+", " ", name).strip().lstrip("@").strip()
    return n if org_key(n) else None


def _aliases_path() -> Path:
    s = get_settings()
    explicit = getattr(s, "org_aliases_path", None)
    if explicit:
        return Path(explicit)
    near_thesis = Path(s.thesis_path).parent / "org_aliases.yaml"
    return near_thesis if near_thesis.exists() else ROOT / "config" / "org_aliases.yaml"


@lru_cache
def alias_map(path: str | None = None) -> dict[str, str]:
    """alias key -> canonical name (canonical names are aliases of themselves)."""
    p = Path(path) if path else _aliases_path()
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, str] = {}
    for canonical, aliases in data.items():
        for a in [canonical, *(aliases or [])]:
            k = org_key(str(a))
            if k:
                out.setdefault(k, str(canonical))
    return out


@lru_cache
def _distinctive(path: str | None = None) -> list[tuple[re.Pattern[str], str]]:
    pats = []
    for k, canonical in alias_map(path).items():
        if " " in k or len(k) >= DISTINCTIVE_MIN_LEN:
            pats.append((len(k), re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])"), canonical))
    pats.sort(key=lambda t: -t[0])
    return [(p, c) for _, p, c in pats]


def normalize_org(name: str | None, *, path: str | None = None) -> str | None:
    """Canonical organization name for a free-text affiliation (see module docstring)."""
    cleaned = clean_org(name)
    if cleaned is None:
        return None
    amap = alias_map(path)
    key = org_key(cleaned)
    if key in amap:
        return amap[key]
    for seg in _SEGMENT_SPLIT.split(" " + cleaned):
        k = org_key(seg)
        if k and k in amap:
            return amap[k]
    for pat, canonical in _distinctive(path):
        if pat.search(key):
            return canonical
    return cleaned


def same_org(a: str | None, b: str | None) -> bool:
    """True when two affiliation strings normalize to the same organization."""
    na, nb = normalize_org(a), normalize_org(b)
    return bool(na and nb) and org_key(na) == org_key(nb)


def normalized_affiliations(current: str | None, prior: list[str] | None) -> dict[str, object]:
    """Value stored in ``Person.attrs['affiliations_normalized']`` (raw strings stay on the row)."""
    seen: set[str] = set()
    norm_prior: list[str] = []
    for a in prior or []:
        n = normalize_org(a)
        if n and org_key(n) not in seen:
            seen.add(org_key(n))
            norm_prior.append(n)
    return {"current_company": normalize_org(current), "prior_affiliations": norm_prior}


__all__ = ["alias_map", "clean_org", "normalize_org", "normalized_affiliations", "org_key", "same_org"]
