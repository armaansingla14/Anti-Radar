"""meeting recommendations (Who Should We Meet, M3)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('meeting_recommendations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('week', sa.String(length=10), nullable=False),
    sa.Column('person_id', sa.UUID(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('priority', sa.String(length=10), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('evidence_count', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.String(length=20), nullable=False),
    sa.Column('formation_candidate_id', sa.UUID(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('week', 'person_id', name='uq_meeting_week_person')
    )
    op.create_index('ix_meeting_week_rank', 'meeting_recommendations', ['week', 'rank'], unique=False)
    op.create_index(op.f('ix_meeting_recommendations_person_id'), 'meeting_recommendations', ['person_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meeting_recommendations_person_id'), table_name='meeting_recommendations')
    op.drop_index('ix_meeting_week_rank', table_name='meeting_recommendations')
    op.drop_table('meeting_recommendations')
