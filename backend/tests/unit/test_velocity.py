"""Velocity math: per-day rates, endpoint provenance, acceleration text and company aggregation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from radar.scoring.velocity import (
    BACKFILL,
    INSUFFICIENT,
    OBSERVED,
    aggregate_velocity,
    compute_velocity,
    fmt_rate,
    resolve_endpoint,
)

DAY = date(2026, 10, 6)  # a Tuesday


def daily(start_value: float, per_day: list[float], end: date = DAY) -> list[tuple[date, float]]:
    """Cumulative daily series ending at ``end`` with the given daily gains (oldest first)."""
    out, v = [], start_value
    start = end - timedelta(days=len(per_day))
    out.append((start, v))
    for i, g in enumerate(per_day, 1):
        v += g
        out.append((start + timedelta(days=i), v))
    return out


def test_observed_star_velocity_and_acceleration_text():
    # 30 days at 12 stars/day, then the last 7 days at 61 stars/day.
    stars = daily(100, [12] * 30 + [61] * 7)
    v = compute_velocity({"stars": stars}, DAY, observed_days={d for d, _ in stars})
    assert v["star_velocity_7d"] == 61 and v["star_velocity_7d_basis"] == OBSERVED
    assert v["star_velocity_prev_7d"] == 12
    assert v["star_velocity_30d"] == pytest.approx((61 * 7 + 12 * 23) / 30, abs=1e-4)
    assert v["star_acceleration"] == 49 and v["star_acceleration_basis"] == OBSERVED
    assert v["star_acceleration_text"] == "12 → 61 stars/day"


def test_insufficient_history_is_none_never_zero():
    stars = daily(100, [5] * 3)  # only 3 days of history
    v = compute_velocity({"stars": stars}, DAY, observed_days={d for d, _ in stars})
    for key in ("star_velocity_7d", "star_velocity_30d", "star_acceleration", "commit_velocity_7d",
                "contributor_velocity_30d", "release_velocity_90d", "issue_velocity_30d"):
        assert v[key] is None and v[f"{key}_basis"] == INSUFFICIENT
    assert v["star_acceleration_text"] is None
    empty = compute_velocity({}, DAY)
    assert empty["star_velocity_7d"] is None and empty["star_velocity_7d_basis"] == INSUFFICIENT


def test_backfill_step_series_and_basis_mixing():
    # GitHub star history: change points only (backfill); today's observed snapshot.
    stars = [(DAY - timedelta(days=40), 50.0), (DAY - timedelta(days=20), 80.0), (DAY - timedelta(days=3), 95.0),
             (DAY, 100.0)]
    v = compute_velocity({"stars": stars}, DAY, observed_days={DAY})
    # day-7 lies between backfill points (-20 -> -3): flat at 80. day-30: flat at 50 (next point is backfill).
    assert v["star_velocity_7d"] == pytest.approx(20 / 7, abs=1e-4)
    assert v["star_velocity_30d"] == pytest.approx(50 / 30, abs=1e-4)
    assert v["star_velocity_7d_basis"] == BACKFILL  # one endpoint observed, one backfill
    # Without provenance everything is labelled backfill (the weaker claim).
    assert compute_velocity({"stars": stars}, DAY)["star_velocity_7d_basis"] == BACKFILL


def test_observed_only_gaps_are_not_bridged():
    # Two observed snapshots 20 days apart with different values: the value 7 days ago is unknown.
    pts = [(DAY - timedelta(days=20), 10.0), (DAY, 30.0)]
    obs = {d for d, _ in pts}
    assert resolve_endpoint(pts, DAY - timedelta(days=7), observed=obs, step=True) is None
    # Same value on both sides of the gap: the count was flat.
    flat = [(DAY - timedelta(days=20), 10.0), (DAY, 10.0)]
    e = resolve_endpoint(flat, DAY - timedelta(days=7), observed={d for d, _ in flat}, step=True)
    assert e is not None and e.value == 10 and e.basis == OBSERVED
    # Non-step (level) metrics never step-fill; a nearby point within tolerance is used at its own date.
    issues = [(DAY - timedelta(days=31), 20.0), (DAY, 14.0)]
    v = compute_velocity({"open_issues": issues}, DAY, observed_days={d for d, _ in issues})
    assert v["issue_velocity_30d"] == pytest.approx(-6 / 31, abs=1e-4)
    far = [(DAY - timedelta(days=36), 20.0), (DAY, 14.0)]
    assert compute_velocity({"open_issues": far}, DAY)["issue_velocity_30d"] is None


def test_new_repo_starts_at_zero_stars():
    created = DAY - timedelta(days=4)
    stars = [(created, 0.0), (DAY - timedelta(days=1), 70.0), (DAY, 80.0)]
    v = compute_velocity({"stars": stars}, DAY, observed_days={DAY}, created=created)
    assert v["star_velocity_7d"] == pytest.approx(80 / 7, abs=1e-4)
    assert v["star_velocity_prev_7d"] == 0
    assert v["star_acceleration_text"] == "0 → 11 stars/day"


def test_commit_velocity_uses_complete_weeks():
    # Week starts are Sundays; 2026-10-04 is the current (partial) week and is ignored.
    sundays = [date(2026, 10, 4) - timedelta(days=7 * k) for k in range(8)][::-1]
    counts = [7, 7, 7, 7, 14, 14, 21, 999]
    v = compute_velocity({"commits_week": list(zip(sundays, counts, strict=True))}, DAY)
    assert v["commit_velocity_7d"] == 3  # 21 commits in the week of 2026-09-27
    assert v["commit_velocity_prev_7d"] == 2
    assert v["commit_velocity_30d"] == pytest.approx((7 + 14 + 14 + 21) / 28, abs=1e-4)
    assert v["commit_acceleration_text"] == "2 → 3 commits/day"
    assert v["commit_velocity_7d_basis"] == BACKFILL
    # Stale weekly data (last complete week a month ago) is insufficient, not a velocity.
    old = [(d - timedelta(days=35), c) for d, c in zip(sundays, counts, strict=True)]
    assert compute_velocity({"commits_week": old}, DAY)["commit_velocity_7d"] is None


def test_release_and_contributor_velocity():
    releases = [(DAY - timedelta(days=120), 0.0), (DAY - timedelta(days=119), 1.0), (DAY - timedelta(days=50), 3.0),
                (DAY, 4.0)]
    contributors = [(DAY - timedelta(days=60), 2.0), (DAY - timedelta(days=10), 5.0), (DAY, 6.0)]
    v = compute_velocity({"releases_total": releases, "contributors": contributors}, DAY, observed_days={DAY})
    assert v["release_velocity_90d"] == pytest.approx(3 / 90, abs=1e-4)
    assert v["contributor_velocity_30d"] == pytest.approx(4 / 30, abs=1e-4)


def test_aggregate_sums_known_repos_and_keeps_basis_honest():
    a = compute_velocity({"stars": daily(0, [10] * 14)}, DAY, observed_days={d for d, _ in daily(0, [10] * 14)})
    b = compute_velocity({"stars": daily(0, [2] * 7 + [4] * 7)}, DAY)  # unknown provenance -> backfill
    c = compute_velocity({}, DAY)  # no history at all
    agg = aggregate_velocity([a, b, c])
    assert agg["repos"] == 3
    assert agg["star_velocity_7d"] == 14 and agg["star_velocity_7d_basis"] == BACKFILL
    assert agg["coverage"]["star_velocity_7d"] == 2
    assert agg["star_acceleration"] == 2 and agg["star_acceleration_text"] == "12 → 14 stars/day"
    assert agg["star_velocity_30d"] is None and agg["star_velocity_30d_basis"] == INSUFFICIENT
    only_observed = aggregate_velocity([a])
    assert only_observed["star_velocity_7d_basis"] == OBSERVED
    none = aggregate_velocity([])
    assert none["star_velocity_7d"] is None and none["star_acceleration_text"] is None


@pytest.mark.parametrize(("v", "s"), [(12.0, "12"), (61.4, "61"), (2.25, "2.2"), (0.0, "0"), (-0.01, "0"),
                                      (1234.0, "1,234"), (0.5, "0.5")])
def test_fmt_rate(v, s):
    assert fmt_rate(v) == s
