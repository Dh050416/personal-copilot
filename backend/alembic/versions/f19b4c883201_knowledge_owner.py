"""Fence stale ingestion writers."""
import sqlalchemy as sa
from alembic import op

revision = 'f19b4c883201'
down_revision = 'e912a7c4b102'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_documents', sa.Column('ingest_owner', sa.String(32), nullable=True))


def downgrade():
    op.drop_column('knowledge_documents', 'ingest_owner')
