"""Novelty: newly on the radar *and* unlike what we already track."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from radar.scoring.features import Component

_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "using",
        "use",
        "your",
        "our",
        "are",
        "was",
        "were",
        "has",
        "have",
        "its",
        "their",
        "about",
        "based",
        "open",
        "source",
        "framework",
        "library",
        "project",
        "tool",
        "tools",
        "toolkit",
        "code",
        "repo",
        "repository",
        "implementation",
        "official",
        "github",
        "python",
        "new",
        "fast",
        "simple",
        "easy",
        "built",
        "build",
        "support",
        "data",
        "model",
        "models",
        "system",
        "systems",
    ]
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]{2,}")


def tokenize(*texts: str | None) -> set[str]:
    out: set[str] = set()
    for t in texts:
        if t:
            out.update(w for w in _TOKEN.findall(t.lower()) if w not in _STOP)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def recency(first_seen: datetime | None, today: date, half_life_days: float) -> float | None:
    if first_seen is None:
        return None
    fs = first_seen if first_seen.tzinfo else first_seen.replace(tzinfo=UTC)
    age = max((today - fs.date()).days, 0)
    return 0.5 ** (age / half_life_days)


def novelty(
    tokens: set[str],
    first_seen: datetime | None,
    earlier: list[tuple[str, set[str]]],
    today: date,
    half_life_days: float,
    max_points: float = 10,
) -> Component:
    """``earlier`` = (name, tokens) of entities first seen before this one."""
    rec = recency(first_seen, today, half_life_days)
    best_name, best_sim = None, 0.0
    for name, toks in earlier:
        s = jaccard(tokens, toks)
        if s > best_sim:
            best_name, best_sim = name, s
    unknown_text = not tokens
    uniq = 0.5 if unknown_text else 1 - best_sim
    rec_v = rec if rec is not None else 0.0
    value = max_points * (0.5 * rec_v + 0.5 * uniq)
    age = None if first_seen is None else max((today - first_seen.date()).days, 0)
    raw = {
        "recency": None if rec is None else round(rec, 4),
        "first_seen_days_ago": age,
        "max_similarity": round(best_sim, 4),
        "most_similar": best_name,
        "compared_against": len(earlier),
    }
    parts = [f"first seen {age} days ago" if age is not None else "first-seen date unknown"]
    if unknown_text:
        parts.append("no descriptive text to compare, uniqueness assumed neutral")
    elif best_name:
        parts.append(f"closest existing entity '{best_name}' at {best_sim:.2f} similarity")
    else:
        parts.append("no similar entity on the radar")
    text = "; ".join(parts)
    return Component("novelty", "Novelty", value, max_points, text[0].upper() + text[1:] + ".", raw)
