"""Persist knowledge sources and embedding provenance."""
import sqlalchemy as sa
from alembic import op

revision = 'e912a7c4b102'
down_revision = 'd80c9bfa2101'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_documents', sa.Column('source_bytes', sa.LargeBinary(), nullable=True))
    op.add_column('knowledge_documents', sa.Column('embedding_model', sa.Text(), nullable=True))
    op.add_column('knowledge_documents', sa.Column('pipeline_version', sa.String(64), nullable=True))


def downgrade():
    op.drop_column('knowledge_documents', 'pipeline_version')
    op.drop_column('knowledge_documents', 'embedding_model')
    op.drop_column('knowledge_documents', 'source_bytes')
