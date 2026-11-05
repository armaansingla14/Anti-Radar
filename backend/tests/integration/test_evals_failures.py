"""The failure ledger: documented failures, regression test references, reviews."""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select

from radar.evals.cases import load_cases
from radar.evals.failures import (
    DOCUMENTED,
    create_failure,
    display_number,
    get_failure,
    list_failures,
    review_failure,
    review_history,
    seed_documented_failures,
)
from radar.models import AgentFailure, ReviewDecision

BACKEND = Path(__file__).resolve().parents[2]
NODE_RE = re.compile(r"^(?P<path>tests/[\w/]+\.py)::(?P<name>\w+)(\[(?P<param>[\w.-]+)\])?$")


def test_seeding_is_idempotent_with_documented_statuses(db):
    assert seed_documented_failures(db) == {"created": 20, "updated": 0}
    assert seed_documented_failures(db) == {"created": 0, "updated": 0}
    rows = list_failures(db)
    assert [f.number for f in rows] == list(range(1, 21)) and all(f.origin == "documented" for f in rows)
    by_ref = {f.source_ref: f for f in rows}
    assert by_ref["docs/REAL_WORLD_EVALUATION.md §6.1"].status == "FIXED"
    assert by_ref["docs/REAL_WORLD_EVALUATION.md §6.1"].category == "MISSING_COUNTEREVIDENCE"
    assert by_ref["docs/REAL_WORLD_EVALUATION.md §6.2"].status == "CONFIRMED"
    assert "Mitigated" in by_ref["docs/REAL_WORLD_EVALUATION.md §6.2"].resolution
    smg = by_ref["docs/REAL_WORLD_EVALUATION.md §6.3"]
    assert smg.status == "CONFIRMED" and smg.regression_test is None and "No test guards" in smg.evidence_summary
    luo = by_ref["docs/REAL_WORLD_EVALUATION.md §6.8"]
    assert luo.status == "CONFIRMED" and luo.regression_test is None
    why = by_ref["docs/FINAL_APPLICATION_AUDIT.md #8"]
    assert why.regression_case == "why-anti-copy-001" and "no pytest test existed" in why.evidence_summary
    bots = next(f for f in rows if f.category == "ENTITY_RESOLUTION_ERROR")
    assert bots.severity == "HIGH" and bots.status == "FIXED" and bots.agent == "ENTITY RESOLUTION"
    assert display_number(41) == "FAILURE 0041"


def test_every_regression_reference_resolves():
    case_ids = {c.id for c in load_cases()}
    refs = []
    for d in DOCUMENTED:
        refs += [t for t in [d.get("regression_test"), *(d.get("related_tests") or [])] if t]
        if d.get("regression_case"):
            assert d["regression_case"] in case_ids, d["regression_case"]
    assert len(refs) >= 15
    for ref in refs:
        m = NODE_RE.match(ref)
        assert m, ref
        src = (BACKEND / m["path"]).read_text()
        assert re.search(rf"^def {m['name']}\(", src, re.MULTILINE), ref
        if m["param"]:
            assert m["param"] in case_ids, ref


def test_review_persists_and_survives_reseed(db):
    seed_documented_failures(db)
    f = review_failure(db, 3, status="FIXED", note="contributor coverage ingested", reviewer="ana")
    assert f.status == "FIXED" and "[ana " in f.resolution and "contributor coverage ingested" in f.resolution
    hist = review_history(db, 3)
    assert [(r.decision, r.note, r.reviewer) for r in hist] == [("FIXED", "contributor coverage ingested", "ana")]
    assert db.scalar(select(func.count()).select_from(ReviewDecision)) == 1
    seed_documented_failures(db)
    again = get_failure(db, 3)
    assert again.status == "FIXED" and "contributor coverage ingested" in again.resolution


def test_create_failure_numbers_and_validation(db):
    seed_documented_failures(db)
    f = create_failure(db, agent="RADAR ANALYST", claim_text="x", category="UNSUPPORTED_CLAIM", severity="LOW")
    assert f.number == 21 and f.status == "OPEN" and f.origin == "reviewer" and f.detected_at is not None
    import pytest

    with pytest.raises(ValueError):
        create_failure(db, agent="RADAR ANALYST", claim_text="x", category="NOPE", severity="LOW")
    with pytest.raises(ValueError):
        review_failure(db, 11, status="MAYBE", note="x")
    assert list_failures(db, status="OPEN", agent="RADAR ANALYST")[0].number == 21
    assert len(list_failures(db, category="SOURCE_QUALITY")) == 2  # smg-project is ATTRIBUTION_ERROR
    assert db.scalar(select(func.count()).select_from(AgentFailure)) == 21
