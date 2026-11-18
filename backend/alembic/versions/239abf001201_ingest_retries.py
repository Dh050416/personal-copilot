"""Persist bounded ingestion retry scheduling."""
import sqlalchemy as sa
from alembic import op

revision = '239abf001201'
down_revision = '129aef843201'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_documents', sa.Column('ingest_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('knowledge_documents', sa.Column('retry_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('knowledge_documents', 'retry_at')
    op.drop_column('knowledge_documents', 'ingest_attempts')
