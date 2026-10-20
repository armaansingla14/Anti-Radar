"""Brief history (GET /api/brief/archive, GET /api/brief/day/{date}) and the morning-brief summary guard
(no leaked internal ids; only listed items)."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from radar.db import session_scope
from radar.llm import narratives
from radar.models import ResearchReport

DEMO = {"include_demo": "true"}


def _reports() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(ResearchReport)
                        .where(ResearchReport.kind == "morning_brief")) or 0


def test_no_stored_brief_is_reported_never_generated(seeded, client):
    assert client.get("/api/brief/archive").json() == []
    for path in ("/api/brief/day/today", "/api/brief/day/2026-10-06", "/api/brief/day/2020-01-01"):
        r = client.get(path)
        assert r.status_code == 404 and r.json() == {"detail": "no_brief_for_date"}, path
    assert _reports() == 0  # reading history never writes a brief
    assert client.get("/api/brief/day/2026-13-45").status_code == 422
    assert client.get("/api/brief/day/yesterday").status_code == 422


def test_persisted_brief_is_served_as_stored(seeded, client, monkeypatch):
    from radar import ops
    from radar.services import brief

    real = brief.morning_brief
    monkeypatch.setattr(brief, "morning_brief", lambda s, **k: real(s, include_demo=True))  # the seed is fictional

    res = ops.persist_morning_brief()
    assert res["date"] == "2026-10-06"
    ops.persist_morning_brief()  # same day again: replaced in place, still one per day
    assert _reports() == 1

    archive = client.get("/api/brief/archive").json()
    assert len(archive) == 1
    a = archive[0]
    assert a["date"] == "2026-10-06" and a["id"] == res["id"] and a["title"].startswith("Morning Brief")
    assert a["items"] == res["items"] >= 3 and len(a["headlines"]) == 3

    today = client.get("/api/brief/day/today").json()
    dated = client.get("/api/brief/day/2026-10-06").json()
    assert today == dated and today["date"] == "2026-10-06" and today["markdown"]
    assert [i["text"] for i in today["what_changed"]] == a["headlines"] + [
        i["text"] for i in today["what_changed"][3:]]
    assert client.get("/api/brief/day/2026-10-05").status_code == 404

    # The stored record is returned verbatim — not recomposed from today's data.
    with session_scope() as s:
        rep = s.scalar(select(ResearchReport).where(ResearchReport.kind == "morning_brief"))
        body = dict(rep.body)
        body["what_changed"] = [{**body["what_changed"][0], "text": "Stored line."}]
        rep.body = body
    again = client.get("/api/brief/day/2026-10-06").json()
    assert [i["text"] for i in again["what_changed"]] == ["Stored line."]
    assert client.get("/api/brief/archive").json()[0]["headlines"] == ["Stored line."]


def test_older_stored_shape_still_served(seeded, client):
    with session_scope() as s:
        s.add(ResearchReport(kind="morning_brief", title="Morning Brief — Monday, October 5, 2026",
                             body={"date": "2026-10-05", "what_changed": [{"n": 1, "text": "Old line."}],
                                   "founders_to_know": [{"broken": True}]},
                             markdown="# OLD", generator="template", status="complete"))
    b = client.get("/api/brief/day/2026-10-05").json()
    assert b["date"] == "2026-10-05" and b["markdown"] == "# OLD"
    assert [i["text"] for i in b["what_changed"]] == ["Old line."] and b["founders_to_know"] == []


def test_brief_what_changed_includes_formation_and_meet(seeded, client):
    b = client.get("/api/brief", params=DEMO).json()
    texts = [i["text"] for i in b["what_changed"]]
    assert len(texts) <= 5
    assert any(t.startswith("Worth meeting this week:") for t in texts), texts
    assert any(t.split(" (score")[0].upper() in ("EMERGING TEAM", "POSSIBLE FORMATION",
                                                  "FORMATION SIGNALS DETECTED") for t in texts), texts
    assert not any("founded" in t.lower() for t in texts)
    assert all(i["evidence"] and i["entity_refs"] for i in b["what_changed"])


# ── summary guard ────────────────────────────────────────────────────────────

ITEMS = [{"n": 1, "text": "Emerging team around Halcyon Dynamics.", "evidence": ["sig-a"]},
         {"n": 2, "text": "Unitree G1 humanoid repository gained 500 stars.", "evidence": ["sig-b"]},
         {"n": 3, "text": "Elias Brandt (55) surfaced.", "evidence": ["sig-c"]}]


def test_clean_brief_summary_strips_or_rejects_ids():
    c = narratives.clean_brief_summary
    assert c("Halcyon leads (I1). Brandt moved [2, 3].", ITEMS) == "Halcyon leads. Brandt moved."
    assert c("Halcyon grew [items 1 and 2].", ITEMS) == "Halcyon grew."
    assert c("I2 says six people but names only four.", ITEMS) is None
    assert c("See item 3 for detail.", ITEMS) is None
    assert c("Signal 3f2b8c1e-1111-2222-3333-444455556666 rose.", ITEMS) is None
    assert c("Unitree G1 grew (G1).", ITEMS) == "Unitree G1 grew (G1)."  # G1 is in an item: a product name
    assert c("Elias Brandt (55) surfaced in 2026.", ITEMS) == "Elias Brandt (55) surfaced in 2026."


class _Fake:
    def __init__(self, text: str, numbers: list[int]):
        self.out = narratives._MorningOut(text=text, item_numbers=numbers)

    def structured(self, *a, **k):
        return SimpleNamespace(parsed=self.out, model="fake-model", cached=False)


def test_morning_brief_summary_falls_back_on_leaks_or_unlisted_items():
    leaked = narratives.morning_brief_summary(ITEMS, provider=_Fake("I2 says six people.", [2]))
    assert leaked["generator"] == "template" and "I2" not in leaked["text"]
    unlisted = narratives.morning_brief_summary(ITEMS, provider=_Fake("Halcyon is forming.", [7]))
    assert unlisted["generator"] == "template"
    ok = narratives.morning_brief_summary(ITEMS, provider=_Fake("Halcyon is forming (I1); Brandt too.", [1, 3, 9]))
    assert ok["generator"] == "llm" and ok["epistemic"] == "MODEL_OPINION"
    assert ok["text"] == "Halcyon is forming; Brandt too." and ok["citations"] == ["sig-a", "sig-c"]
