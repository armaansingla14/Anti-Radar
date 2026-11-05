"""Every regression case in tests/evals/cases runs as a pytest test (template baseline, no network).

Pure cases run without Postgres; cases whose task builds database fixtures request the ``db`` fixture and run
inside a savepoint that is always rolled back.
"""

from __future__ import annotations

import pytest

from radar.evals.cases import CASES_DIR, load_cases
from radar.evals.runner import TASKS, run_case

CASES = load_cases()


def test_case_files_validate_and_ids_are_unique():
    files = sorted(CASES_DIR.glob("*.yaml"))
    assert files and len(CASES) == len(files)
    ids = [c.id for c in CASES]
    assert len(set(ids)) == len(ids)
    assert all(c.path is not None and c.path.stem == c.id for c in CASES)  # file name is the id
    assert all(c.task in TASKS for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_regression_case(case, request):
    session = request.getfixturevalue("db") if TASKS[case.task].needs_db else None
    outcome = run_case(session, case)
    assert outcome.passed, "\n".join(outcome.failures) or outcome.error
