"""Card trust semantics: confidence_for, why-now sentence, recent change (services.presenters)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from radar.schemas.api import EvidenceItem
from radar.services.presenters import ago_phrase, card_trust, confidence_for

NOW = datetime(2026, 10, 20, 18, tzinfo=UTC)


def ev(title, src, days=None, weight=None, sid="s"):
    return EvidenceItem(signal_id=f"{sid}-{title}", title=title, source_type=src,
                        source_url="https://example.com/x", weight=weight,
                        event_at=None if days is None else NOW - timedelta(days=days))


def test_confidence_thresholds_and_near_misses():
    assert confidence_for(["github", "research", "hiring"], 4) == "HIGH CONFIDENCE"
    assert confidence_for(["github", "research", "hiring"], 3) == "MEDIUM CONFIDENCE"  # 1 item short
    assert confidence_for(["github", "research"], 10) == "MEDIUM CONFIDENCE"
    assert confidence_for(["github"], 10) == "EARLY SIGNAL"
    assert confidence_for([], 0) == "EARLY SIGNAL"
    assert confidence_for(3, 5, verified_share=0.4) == "MEDIUM CONFIDENCE"  # mostly uncited evidence
    assert confidence_for(3, 5, verified_share=0.5) == "HIGH CONFIDENCE"
    assert confidence_for(["github", "github", "github"], 9) == "EARLY SIGNAL"  # repeats are not independent


def test_ago_phrase():
    assert ago_phrase(NOW, NOW) == "today"
    assert ago_phrase(NOW - timedelta(days=1), NOW) == "yesterday"
    assert ago_phrase(NOW - timedelta(days=41), NOW) == "6 weeks ago"
    assert ago_phrase(NOW - timedelta(days=5), NOW) == "5 days ago"


def test_card_trust_from_dated_evidence():
    t = card_trust([
        ev("New GitHub org created", "github", 41, 0.9),
        ev("3 contributors joined", "github", 10, 0.7),
        ev("First controls role posted", "jobs", 1, 0.8),
        ev("Paper published", "arxiv", 30, 0.2),
        EvidenceItem(title="Signal collision: 3 independent sources", source_type="system"),
    ], NOW)
    assert t.confidence == "HIGH CONFIDENCE" and t.sources_count == 3
    assert t.why_now_sentence == ("First controls role posted (yesterday); 3 contributors joined (10 days ago); "
                                  "New GitHub org created (6 weeks ago)")
    assert t.recent_change == "First controls role posted (yesterday)"


def test_card_trust_without_dates_never_invents():
    t = card_trust([EvidenceItem(title="GitHub stars +600 in 30 days", source_type="github")], NOW)
    assert t.confidence == "EARLY SIGNAL" and t.recent_change is None
    assert t.why_now_sentence == "GitHub stars +600 in 30 days"  # a measured window, stated as is
    empty = card_trust([], NOW)
    assert empty.why_now_sentence == "" and empty.recent_change is None and empty.sources_count == 0


def test_card_trust_ignores_future_and_dedupes():
    a = ev("Release v1", "github", 2)
    t = card_trust([a, a, ev("Future thing", "jobs", -10)], NOW)
    assert "Future thing" not in t.why_now_sentence and t.recent_change == "Release v1 (2 days ago)"
    assert t.sources_count == 1  # evidence dated after the as-of time is ignored entirely


def test_observation_dated_signals_are_measurements_not_changes():
    m = ev("+616 stars in 30 days", "github", 0)
    t = card_trust([m, ev("Repository acme/motus created", "github", 40, 0.2)], NOW, observed={m.signal_id})
    assert t.why_now_sentence == "+616 stars in 30 days; Repository acme/motus created (6 weeks ago)"
    assert t.recent_change == "Repository acme/motus created (6 weeks ago)"  # never "today" for a measurement


def test_one_story_is_told_once():
    t = card_trust([
        ev("Code for arXiv paper: FDE-Gym", "arxiv", 5, 0.5),
        ev("FDE-Gym", "arxiv", 5, 0.5),
        ev("kevinzakka created a new repository: kevinzakka/mjbatch", "github", 40, 0.4),
        ev("Repository kevinzakka/mjbatch created", "github", 40, 0.3),
    ], NOW)
    assert t.why_now_sentence == ("Code for arXiv paper: FDE-Gym (5 days ago); "
                                  "kevinzakka created a new repository: kevinzakka/mjbatch (6 weeks ago)")
