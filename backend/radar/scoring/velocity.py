"""Velocity and acceleration from stored metric series (pure, network- and DB-free).

Units are *per day*: ``star_velocity_7d`` is stars gained per day over the last 7 days.
Every value carries a sibling ``<key>_basis``:

* ``observed``  — both window endpoints are our own daily snapshots;
* ``backfill``  — at least one endpoint comes from GitHub's own history (stargazers/history,
  stats/commit_activity, stats/contributors, release dates): real, reconstructed data;
* ``insufficient_history`` — an endpoint is unknown; the value is ``None``. Nothing is ever
  interpolated, extrapolated or defaulted to zero.

Endpoint rules (``resolve_endpoint``):

1. a point dated exactly on the target day;
2. for cumulative *step* metrics (stars, contributors, releases_total) the last point before the
   target, provided the series is known to be flat up to the target: either the next point is
   backfill (backfill series record every change day, so none happened in between) or the next
   point has the same value;
3. otherwise the last point within ``tolerance_days`` of the target (velocity is then divided by
   the real distance between the two points, never the nominal window);
4. stars and releases are 0 before the repository was created.

Star backfill older than ~90 days may be thinned (``github.compact_series``); windows here reach
at most 14 days back for stars, well inside the fully kept range.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

OBSERVED = "observed"
BACKFILL = "backfill"
INSUFFICIENT = "insufficient_history"
_CREATION = "creation"  # endpoint fixed by the repo's creation date; inherits the other endpoint's basis

TOLERANCE_DAYS = 2
STEP_METRICS = frozenset({"stars", "contributors", "releases_total"})
ZERO_AT_CREATION = frozenset({"stars", "releases_total"})
COMMIT_WEEK_MAX_AGE_DAYS = 14  # the latest complete commit week must start within this many days

VELOCITY_KEYS = (
    "star_velocity_7d",
    "star_velocity_30d",
    "star_velocity_prev_7d",
    "star_acceleration",
    "commit_velocity_7d",
    "commit_velocity_30d",
    "commit_velocity_prev_7d",
    "commit_acceleration",
    "contributor_velocity_30d",
    "release_velocity_90d",
    "issue_velocity_30d",
)
# key -> (metric, window days, end offset days) for simple window velocities on cumulative series.
WINDOWS: dict[str, tuple[str, int, int]] = {
    "star_velocity_7d": ("stars", 7, 0),
    "star_velocity_30d": ("stars", 30, 0),
    "star_velocity_prev_7d": ("stars", 7, 7),
    "contributor_velocity_30d": ("contributors", 30, 0),
    "release_velocity_90d": ("releases_total", 90, 0),
    "issue_velocity_30d": ("open_issues", 30, 0),  # net change of open issues (PRs excluded)
}
ACCELERATIONS = {
    "star_acceleration": ("star_velocity_prev_7d", "star_velocity_7d", "stars/day"),
    "commit_acceleration": ("commit_velocity_prev_7d", "commit_velocity_7d", "commits/day"),
}

Point = tuple[date, float]


@dataclass(frozen=True)
class Endpoint:
    value: float
    day: date  # the date the value is exact for
    basis: str


def _combine(*bases: str) -> str:
    real = {b for b in bases if b != _CREATION}
    if INSUFFICIENT in real:
        return INSUFFICIENT
    return OBSERVED if real == {OBSERVED} else BACKFILL


def fmt_rate(v: float) -> str:
    """Human rate: integers from 10 up, one decimal below, never '-0'."""
    if abs(v) >= 10:
        out = f"{v:,.0f}"
    else:
        out = f"{v:.1f}".rstrip("0").rstrip(".")
    return "0" if out in ("-0", "") else out


def resolve_endpoint(
    points: Sequence[Point], target: date, *, observed: set[date] | frozenset[date] = frozenset(),
    step: bool = False, created: date | None = None, zero_at_creation: bool = False,
    tolerance_days: int = TOLERANCE_DAYS,
) -> Endpoint | None:
    """Exact value of a series on ``target`` under the module's endpoint rules, or None."""
    if zero_at_creation and created is not None and target < created:
        return Endpoint(0.0, target, _CREATION)
    i = bisect_right(points, (target, float("inf")))
    if i == 0:
        return None

    def basis(d: date) -> str:
        return OBSERVED if d in observed else BACKFILL

    d, v = points[i - 1]
    if d == target:
        return Endpoint(v, target, basis(d))
    if step and i < len(points):
        nd, nv = points[i]
        if nd not in observed or nv == v:
            return Endpoint(v, target, _combine(basis(d), basis(nd)))
    if (target - d).days <= tolerance_days:
        return Endpoint(v, d, basis(d))
    return None


def _window(points: Sequence[Point], day: date, days: int, end_offset: int, *, observed, step, created,
            zero) -> tuple[float | None, str]:
    end_t = day - timedelta(days=end_offset)
    kw: dict[str, Any] = {"observed": observed, "step": step, "created": created, "zero_at_creation": zero}
    end = resolve_endpoint(points, end_t, **kw)
    start = resolve_endpoint(points, end_t - timedelta(days=days), **kw)
    if end is None or start is None:
        return None, INSUFFICIENT
    span = (end.day - start.day).days
    if span <= 0 or span < days / 2:  # tolerance must not turn a 7-day window into a 2-day one
        return None, INSUFFICIENT
    return round((end.value - start.value) / span, 4), _combine(end.basis, start.basis)


def _commit_weeks(points: Sequence[Point], day: date, *, observed, created: date | None,
                  ) -> dict[str, tuple[float | None, str]]:
    """commits_week samples (day = week start). Uses complete weeks only: the current week is partial."""
    out: dict[str, tuple[float | None, str]] = {
        k: (None, INSUFFICIENT) for k in ("commit_velocity_7d", "commit_velocity_30d", "commit_velocity_prev_7d")
    }
    complete = [d for d, _ in points if d + timedelta(days=7) <= day]
    if not complete:
        return out
    last = max(complete)
    if (day - last).days > COMMIT_WEEK_MAX_AGE_DAYS:
        return out  # stale: the latest data does not describe the last week
    by_day = dict(points)

    def week(k: int) -> tuple[float, str] | None:
        w = last - timedelta(days=7 * k)
        if w in by_day:
            return by_day[w], (OBSERVED if w in observed else BACKFILL)
        if created is not None and w + timedelta(days=6) < created:
            return 0.0, _CREATION  # the repo did not exist that week
        return None

    def rate(ks: Iterable[int]) -> tuple[float | None, str]:
        got = [week(k) for k in ks]
        if any(g is None for g in got):
            return None, INSUFFICIENT
        vals = [g for g in got if g is not None]
        return round(sum(v for v, _ in vals) / (7 * len(vals)), 4), _combine(*(b for _, b in vals))

    out["commit_velocity_7d"] = rate([0])
    out["commit_velocity_prev_7d"] = rate([1])
    out["commit_velocity_30d"] = rate(range(4))  # 4 complete weeks = 28 days
    return out


def _accelerate(out: dict[str, Any]) -> None:
    for key, (prev_k, cur_k, unit) in ACCELERATIONS.items():
        prev, cur = out.get(prev_k), out.get(cur_k)
        if prev is None or cur is None:
            out[key], out[f"{key}_basis"], out[f"{key}_text"] = None, INSUFFICIENT, None
            continue
        out[key] = round(cur - prev, 4)
        out[f"{key}_basis"] = _combine(out[f"{prev_k}_basis"], out[f"{cur_k}_basis"])
        out[f"{key}_text"] = f"{fmt_rate(prev)} → {fmt_rate(cur)} {unit}"


def compute_velocity(
    series: Mapping[str, Sequence[Point]], day: date, *,
    observed_days: set[date] | Mapping[str, set[date]] | None = None, created: date | None = None,
) -> dict[str, Any]:
    """Per-day velocities and accelerations for one repository as of ``day``.

    ``series``: metric -> [(day, value)] sorted by day (stars, contributors, releases_total and
    open_issues are cumulative/level series; commits_week holds weekly counts keyed by week start).
    ``observed_days``: days that are our own snapshots, for all metrics or per metric. Points on
    other days are GitHub backfill. ``None`` means provenance unknown: treated as backfill (the
    weaker claim). ``created``: repository creation date (stars/releases are 0 before it)."""

    def obs(metric: str) -> set[date] | frozenset[date]:
        if observed_days is None:
            return frozenset()
        if isinstance(observed_days, Mapping):
            return observed_days.get(metric, set())
        return observed_days

    out: dict[str, Any] = {}
    for key, (metric, days, end_offset) in WINDOWS.items():
        pts = sorted(series.get(metric) or [])
        out[key], out[f"{key}_basis"] = _window(
            pts, day, days, end_offset, observed=obs(metric), step=metric in STEP_METRICS, created=created,
            zero=metric in ZERO_AT_CREATION,
        )
    for key, (v, b) in _commit_weeks(sorted(series.get("commits_week") or []), day,
                                     observed=obs("commits_week"), created=created).items():
        out[key], out[f"{key}_basis"] = v, b
    _accelerate(out)
    return out


def aggregate_velocity(per_repo: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Company velocity = sum over its repositories with a known value.

    A partial sum is a lower bound, never an estimate: ``coverage[key]`` records how many repos
    contributed. Basis is ``observed`` only if every contributing repo's value is observed.
    Accelerations are recomputed from repos that have both of their windows, so the text
    ("12 → 61 stars/day") always describes one consistent set of repos."""
    out: dict[str, Any] = {"repos": len(per_repo), "coverage": {}}
    for key in VELOCITY_KEYS:
        if key in ACCELERATIONS:
            continue
        known = [(r[key], r.get(f"{key}_basis", BACKFILL)) for r in per_repo if r.get(key) is not None]
        out["coverage"][key] = len(known)
        if not known:
            out[key], out[f"{key}_basis"] = None, INSUFFICIENT
        else:
            out[key] = round(sum(v for v, _ in known), 4)
            out[f"{key}_basis"] = _combine(*(b for _, b in known))
    for key, (prev_k, cur_k, unit) in ACCELERATIONS.items():
        both = [r for r in per_repo if r.get(prev_k) is not None and r.get(cur_k) is not None]
        out["coverage"][key] = len(both)
        if not both:
            out[key], out[f"{key}_basis"], out[f"{key}_text"] = None, INSUFFICIENT, None
            continue
        prev = sum(r[prev_k] for r in both)
        cur = sum(r[cur_k] for r in both)
        out[key] = round(cur - prev, 4)
        out[f"{key}_basis"] = _combine(*(r.get(f"{k}_basis", BACKFILL) for r in both for k in (prev_k, cur_k)))
        out[f"{key}_text"] = f"{fmt_rate(prev)} → {fmt_rate(cur)} {unit}"
    return out
