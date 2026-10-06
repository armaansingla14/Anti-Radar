"""users.prev_visit_at — anchor for the Daily "since last visit" window

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06 15:30:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('prev_visit_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'prev_visit_at')
