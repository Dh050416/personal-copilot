"""Persist cross-instance inputs and immutable approval decisions."""
import sqlalchemy as sa

from alembic import op

revision = 'd80c9bfa2101'
down_revision = 'a4ef23e2f0b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_inputs',
                    sa.Column('sequence', sa.BigInteger(), primary_key=True, autoincrement=True),
                    sa.Column('id', sa.String(32), nullable=False, unique=True),
                    sa.Column('thread_id', sa.Text(), nullable=False),
                    sa.Column('content', sa.Text(), nullable=False))
    op.create_index('ix_agent_inputs_thread', 'agent_inputs', ['thread_id', 'sequence'])
    op.create_table('agent_decisions',
                    sa.Column('thread_id', sa.Text(), primary_key=True),
                    sa.Column('approval_id', sa.Text(), primary_key=True),
                    sa.Column('decision', sa.Text(), nullable=False))


def downgrade():
    op.drop_table('agent_decisions')
    op.drop_index('ix_agent_inputs_thread', table_name='agent_inputs')
    op.drop_table('agent_inputs')
