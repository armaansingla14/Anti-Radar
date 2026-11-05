"""Snapshot chain replay (demo/seed.py): a base snapshot and a dated update, each replayed and derived on its
own day. Two tiny synthetic snapshot directories are written to tmp_path in the committed capture format
(gzipped ProviderBatch payloads + MANIFEST.json)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar import clock
from radar.db import session_scope
from radar.models import Company, FormationHistory, IngestionRun, MeetingRecommendation, ScoreSnapshot, Signal
from radar.providers.base import (
    CompanyRecord,
    MetricPoint,
    NormalizedSignal,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    gh_org_ref,
    gh_user_ref,
    repo_ref,
)

BASE, UPDATE = date(2026, 9, 1), date(2026, 9, 15)


def _at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


def org_batch(org: str, *, day: date, created: date, stars: int, signals: list[NormalizedSignal]) -> ProviderBatch:
    full = f"{org}/core"
    login = f"{org}-dev"
    return ProviderBatch(
        provider="github",
        companies=[CompanyRecord(ref=gh_org_ref(org), name=org.title(), github_org=org, kind="project",
                                 description="Robot manipulation stack", source_url=f"https://github.com/{org}")],
        people=[PersonRecord(ref=gh_user_ref(login), name=f"{org.title()} Dev", github_handle=login,
                             source_url=f"https://github.com/{login}")],
        repositories=[RepoRecord(ref=repo_ref(full), full_name=full, owner=org, owner_type="Organization",
                                 description="Robot manipulation policies", topics=["robotics", "manipulation"],
                                 stars=stars, gh_created_at=_at(created), pushed_at=_at(day),
                                 owner_company=gh_org_ref(org))],
        relations=[Relation(kind="contributor", src=gh_user_ref(login), dst=repo_ref(full),
                            attrs={"contributions": 40})],
        metrics=[MetricPoint(entity=repo_ref(full), metric="stars", day=day, value=stars)],
        signals=signals,
    )


def repo_signal(org: str, title: str, event: date) -> NormalizedSignal:
    return NormalizedSignal(entity=repo_ref(f"{org}/core"), signal_type="repo_created", source_type="github",
                            source_url=f"https://github.com/{org}/core#{title}", title=title, event_at=_at(event),
                            normalized_value=0.6, related=[gh_org_ref(org)])


def write_link(directory: Path, as_of: date, batches: list[ProviderBatch]) -> Path:
    directory.mkdir(parents=True)
    for i, b in enumerate(batches, 1):
        payload = {"provider": b.provider, "params": {}, "run": {"status": "ok"}, "batch": b.model_dump(mode="json")}
        with gzip.open(directory / f"{i:04d}_{b.provider}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(payload, f)
    (directory / "MANIFEST.json").write_text(json.dumps({"kind": "verified_snapshot", "as_of": as_of.isoformat(),
                                                         "captured_at": _at(as_of, 17).isoformat(),
                                                         "files": len(batches)}))
    return directory


@pytest.fixture()
def chain(tmp_path, db_url, monkeypatch):
    from radar.demo.seed import reset_database
    from radar.settings import get_settings

    # The chain pins the data clock to each manifest day; TODAY_OVERRIDE (conftest) would mask the pin.
    monkeypatch.setattr(get_settings(), "today_override", None)
    monkeypatch.setattr(get_settings(), "demo_synthetic", False)
    known = repo_signal("basecorp", "basecorp/core created", BASE - timedelta(days=20))
    base = write_link(tmp_path / "snapshot", BASE, [
        org_batch("basecorp", day=BASE, created=BASE - timedelta(days=20), stars=120, signals=[known])])
    update = write_link(tmp_path / "snapshot_updates" / UPDATE.isoformat(), UPDATE, [
        # basecorp re-observed: the same signal again (deduplicated) plus a fresh metric and a new signal
        org_batch("basecorp", day=UPDATE, created=BASE - timedelta(days=20), stars=180,
                  signals=[known, repo_signal("basecorp", "basecorp v0.2 released", UPDATE - timedelta(days=2))]),
        org_batch("newcorp", day=UPDATE, created=date(2025, 3, 1), stars=40,
                  signals=[repo_signal("newcorp", "newcorp/core created", date(2025, 3, 1))]),
    ])
    reset_database()
    yield [base, update]
    reset_database()
    clock.set_as_of(None)


def _seed(links):
    from radar.demo.seed import seed_demo

    return seed_demo(chain=links, fictional=False, llm_outputs=False)


def test_chain_replays_each_link_on_its_own_day(chain):
    out = _seed(chain)
    assert [x["kind"] for x in out["links"]] == ["base", "update"]
    assert out["snapshot"]["as_of"] == BASE.isoformat()
    with session_scope() as s:
        runs = {r.params["as_of"]: r.params for r in s.scalars(select(IngestionRun).where(
            IngestionRun.provider == "snapshot"))}
        assert {k: (v["kind"], v["link"]) for k, v in runs.items()} == {
            BASE.isoformat(): ("base", 0), UPDATE.isoformat(): ("update", 1)}

        base_co = s.scalar(select(Company).where(Company.github_org == "basecorp"))
        new_co = s.scalar(select(Company).where(Company.github_org == "newcorp"))
        # Bootstrap backfill only for the base link; the update-only org keeps the update day
        # ("new to Radar since the base snapshot") even though GitHub says it was created in 2025.
        assert base_co.first_seen_at.date() <= BASE
        assert new_co.first_seen_at.date() == UPDATE

        snaps = {sn.day: sn for sn in s.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == base_co.id))}
        assert BASE in snaps and UPDATE in snaps
        assert not (snaps[BASE].attention or {}).get("reconstructed")  # the base day stays a live score
        assert not (snaps[UPDATE].attention or {}).get("reconstructed")
        reconstructed = [d for d, sn in snaps.items() if (sn.attention or {}).get("reconstructed")]
        assert reconstructed and max(reconstructed) < BASE  # history only before the base snapshot
        assert all(d in (BASE, UPDATE) for d, sn in snaps.items() if d >= BASE)
        new_days = set(s.scalars(select(ScoreSnapshot.day).where(ScoreSnapshot.entity_id == new_co.id)))
        assert new_days == {UPDATE}

        # Re-observed signal keeps its original detection day; the new one is stamped with the update day.
        detected = {x.title: x.detected_at.date() for x in s.scalars(select(Signal))}
        assert detected["basecorp/core created"] == BASE
        assert detected["basecorp v0.2 released"] == UPDATE

        for link in out["links"]:
            f = link["derived"]["formation"]
            assert "error" not in f, f
            day = date.fromisoformat(link["as_of"])
            n = s.scalar(select(func.count()).select_from(FormationHistory).where(FormationHistory.day == day))
            assert n == f["history"] == f["stored"]
        weeks = [link["derived"]["meet"] for link in out["links"]]
        assert [w["week"] for w in weeks] == ["2026-W36", "2026-W38"] and all("error" not in w for w in weeks)
        stored = set(s.scalars(select(MeetingRecommendation.week).distinct()))
        assert stored <= {"2026-W36", "2026-W38"}
    assert clock.as_of() == UPDATE  # pinned to the newest link


def test_rerun_is_idempotent_and_applies_only_new_links(chain):
    _seed(chain[:1])
    with session_scope() as s:
        assert set(s.scalars(select(ScoreSnapshot.day).where(ScoreSnapshot.day >= BASE))) == {BASE}
    out = _seed(chain)  # the update arrives later: only it is replayed
    assert [x.get("skipped") for x in out["links"]] == ["already replayed", None]
    assert out["links"][1]["kind"] == "update"
    again = _seed(chain)
    assert all(x.get("skipped") == "already replayed" for x in again["links"])
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.provider == "snapshot")) == 2
        base_co = s.scalar(select(Company).where(Company.github_org == "basecorp"))
        base_snap = s.scalar(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == base_co.id,
                                                         ScoreSnapshot.day == BASE))
        assert not (base_snap.attention or {}).get("reconstructed")
    assert clock.as_of() == UPDATE


def test_out_of_order_link_is_refused(chain):
    out = _seed([chain[1]])  # the update alone bootstraps the database
    assert out["links"][0]["kind"] == "base"
    out = _seed(chain)
    assert "older than the newest replayed snapshot" in out["links"][0]["skipped"]
    assert out["links"][1]["skipped"] == "already replayed"


def test_chain_ignores_in_progress_captures_and_orders_by_manifest_day(tmp_path):
    from radar.demo.seed import snapshot_chain

    base = write_link(tmp_path / "snapshot", BASE, [])
    later = write_link(tmp_path / "u" / "b-later", UPDATE, [])
    earlier = write_link(tmp_path / "u" / "z-earlier", BASE + timedelta(days=3), [])
    write_link(tmp_path / "u" / f"{date(2026, 9, 30).isoformat()}.tmp", date(2026, 9, 30), [])
    (tmp_path / "u" / "no-manifest").mkdir()
    assert snapshot_chain(base, tmp_path / "u") == [base, earlier, later]


def test_formation_history_is_recorded_for_every_link(chain):
    """With the labeled fictional set (formation teams) seeded on the base day, both days record history."""
    from radar.demo.seed import seed_demo

    out = seed_demo(chain=chain, fictional=True, llm_outputs=False, history=False)
    assert out["fictional_included"] and "fictional" in out
    with session_scope() as s:
        per_day = dict(s.execute(select(FormationHistory.day, func.count()).group_by(FormationHistory.day)).all())
        assert per_day.get(BASE, 0) >= 1 and per_day.get(UPDATE, 0) >= 1, per_day
        for link in out["links"]:
            assert per_day[date.fromisoformat(link["as_of"])] == link["derived"]["formation"]["stored"]
        halcyon = s.scalar(select(Company).where(Company.name == "Halcyon Dynamics"))
        rows = list(s.scalars(select(FormationHistory).where(FormationHistory.anchor_id == halcyon.id)
                              .order_by(FormationHistory.day)))
        assert [r.day for r in rows] == [BASE, UPDATE] and all(r.is_demo and r.components for r in rows)
        assert set(s.scalars(select(MeetingRecommendation.week).distinct())) == {"2026-W36", "2026-W38"}
