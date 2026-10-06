"""Thesis Fit: how squarely an entity sits inside Anti Fund's themes."""

from __future__ import annotations

from radar.scoring.features import Component
from radar.thesis.loader import Thesis


def thesis_fit(
    theme_scores: dict[str, float],
    topic_scores: dict[str, float],
    topic_evidence: dict[str, list[str]],
    thesis: Thesis,
    prefs: dict[str, float] | None = None,
    stage: str | None = None,
    max_points: float = 15,
) -> Component:
    """max_points * max_theme(weight * score) * stage_mult * pref_mult('thesis:<theme>').

    ``stage=None`` skips the stage multiplier (people have no stage).
    """
    prefs = prefs or {}
    best_theme, best_val = None, 0.0
    for theme, score in theme_scores.items():
        t = thesis.themes.get(theme)
        if t is None:
            continue
        v = t.weight * min(max(score, 0.0), 1.0)
        if v > best_val:
            best_theme, best_val = theme, v
    stage_mult = thesis.stage_multiplier(stage) if stage is not None else 1.0
    raw: dict = {
        "theme_scores": {k: round(v, 3) for k, v in theme_scores.items()},
        "stage": stage,
        "stage_multiplier": stage_mult,
    }
    if best_theme is None:
        return Component("thesis_fit", "Thesis Fit", 0.0, max_points, "No thesis topic matched yet.", raw)
    pref_mult = prefs.get(f"thesis:{best_theme}", 1.0)
    value = min(max_points, max_points * best_val * stage_mult * pref_mult)
    sub_keys = [k for k in topic_scores if k.startswith(best_theme + ".")]
    best_sub = max(sub_keys, key=lambda k: topic_scores[k]) if sub_keys else None
    theme = thesis.themes[best_theme]
    label = theme.label
    if best_sub:
        sub = theme.subcategories.get(best_sub.split(".", 1)[1])
        if sub is not None:
            label = f"{theme.label} / {sub.label}"
    keywords = topic_evidence.get(best_sub, []) if best_sub else []
    raw.update(
        {
            "theme": best_theme,
            "topic": best_sub,
            "theme_weight": theme.weight,
            "pref_multiplier": pref_mult,
            "matched_keywords": keywords,
        }
    )
    expl = f"Best fit: {label} (match {theme_scores[best_theme]:.2f}, theme weight {theme.weight:g})"
    if keywords:
        expl += f"; matched: {', '.join(keywords[:5])}"
    if stage is not None:
        expl += f"; stage '{stage}' x{stage_mult:g}"
    if pref_mult != 1.0:
        expl += f"; feedback preference x{pref_mult:.2f}"
    return Component("thesis_fit", "Thesis Fit", value, max_points, expl + ".", raw)
