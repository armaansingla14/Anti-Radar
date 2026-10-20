"""Retention: evidence pruning, weekly thinning, metadata/snippet trimming, VACUUM."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from radar import clock, retention
from radar.models import ScoreSnapshot, Signal, Source

EV = [{"signal_id": "11111111-1111-1111-1111-111111111111", "title": "x" * 50, "weight": 0.5}]


def _snap(eid: uuid.UUID, day: date, evidence=EV) -> ScoreSnapshot:
    return ScoreSnapshot(
        id=uuid.uuid4(), entity_type="company", entity_id=eid, day=day, score=42.0, version="t",
        components={"acceleration": {"key": "acceleration", "value": 12.5, "max": 20, "evidence": list(evidence),
                                     "explanation": "kept"}},
        collision={"score": 0.7, "evidence": list(evidence)}, attention={"gap": 10},
    )


def test_retention_policy(db):
    today = clock.today()
    eid = uuid.uuid4()
    recent, old = today - timedelta(days=3), today - timedelta(days=20)
    db.add_all([_snap(eid, recent), _snap(eid, old)])
    # >90 days: two ISO weeks with three snapshots each -> one kept per week
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=20)
    ancient = [monday + timedelta(days=d) for d in (0, 2, 4, 7, 8, 9)]
    db.add_all([_snap(eid, d) for d in ancient])
    old_dt = clock.now() - timedelta(days=120)
    db.add(Signal(id=uuid.uuid4(), entity_id=eid, entity_type="company", signal_type="bio_changed",
                  source_type="github", title="t", detected_at=old_dt, event_at=old_dt, dedup_key="d1",
                  meta={"old": "a" * 2000, "new": "b" * 2000, "n": 3}))
    db.add(Signal(id=uuid.uuid4(), entity_id=eid, entity_type="company", signal_type="bio_changed",
                  source_type="github", title="t", detected_at=clock.now(), event_at=clock.now(), dedup_key="d2",
                  meta={"old": "a" * 2000}))
    db.add(Source(id=uuid.uuid4(), url="https://a.dev", source_type="website", snippet="s" * 1000,
                  fetched_at=datetime.now(UTC) - timedelta(days=100)))
    db.add(Source(id=uuid.uuid4(), url="https://b.dev", source_type="website", snippet="s" * 1000,
                  fetched_at=datetime.now(UTC)))
    db.commit()

    out = retention.run()
    assert out["evidence_pruned"] == 1 + 2  # the 20-day-old one + 2 surviving ancient ones
    assert out["snapshots_deleted"] == 4
    assert out["signal_metadata_trimmed"] == 1 and out["source_snippets_trimmed"] == 1
    assert set(out["vacuumed"]) == {"score_snapshots", "signals", "sources"} and out["db_bytes_after"]

    db.expire_all()
    snaps = {s.day: s for s in db.scalars(select(ScoreSnapshot))}
    assert snaps[recent].components["acceleration"]["evidence"] == EV  # recent evidence untouched
    pruned = snaps[old]
    assert pruned.score == 42.0 and pruned.components["acceleration"]["value"] == 12.5
    assert pruned.components["acceleration"]["evidence"] == []
    assert pruned.components["acceleration"]["evidence_ids"] == [EV[0]["signal_id"]]
    assert pruned.collision["evidence"] == [] and pruned.collision["score"] == 0.7
    kept_ancient = sorted(d for d in snaps if d < today - timedelta(days=90))
    assert kept_ancient == [ancient[2], ancient[5]]  # latest day of each ISO week
    sigs = {s.dedup_key: s for s in db.scalars(select(Signal))}
    assert sigs["d1"].meta["_trimmed"] and len(sigs["d1"].meta["old"]) == retention.MAX_STR
    assert sigs["d1"].meta["n"] == 3 and len(sigs["d2"].meta["old"]) == 2000
    snippets = {s.url: s.snippet for s in db.scalars(select(Source))}
    assert len(snippets["https://a.dev"]) == retention.MAX_STR + 1 and len(snippets["https://b.dev"]) == 1000

    again = retention.run(do_vacuum=False)  # idempotent
    assert again["evidence_pruned"] == 0 and again["snapshots_deleted"] == 0
    assert again["signal_metadata_trimmed"] == 0 and again["source_snippets_trimmed"] == 0
