"""read-path indexes (M9 perf): memo data-version maxima, first-seen windows, brief history

* ``score_snapshots.created_at`` / ``signals.created_at`` / ``formation_candidates.updated_at`` /
  ``companies.updated_at`` / ``people.updated_at`` —
  ``services.memo`` reads ``max()`` of these on every memoized request; without an index each is a
  sequential scan that grows with history.
* ``companies.first_seen_at`` / ``people.first_seen_at`` — Unknown Yesterday and ``new_only`` windows.
* ``research_papers.first_seen_at`` — ``recent_papers`` ORs it with the (indexed) ``published_at``.
* ``research_reports (kind, created_at)`` — stored morning briefs (archive / brief by day).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20 18:00:00
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

_INDEXES = [
    ('ix_score_snapshots_created_at', 'score_snapshots', ['created_at']),
    ('ix_signals_created_at', 'signals', ['created_at']),
    ('ix_formation_candidates_updated_at', 'formation_candidates', ['updated_at']),
    ('ix_companies_updated_at', 'companies', ['updated_at']),
    ('ix_people_updated_at', 'people', ['updated_at']),
    ('ix_companies_first_seen_at', 'companies', ['first_seen_at']),
    ('ix_people_first_seen_at', 'people', ['first_seen_at']),
    ('ix_research_papers_first_seen_at', 'research_papers', ['first_seen_at']),
    ('ix_research_reports_kind_created', 'research_reports', ['kind', 'created_at']),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, unique=False)


def downgrade() -> None:
    for name, table, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
