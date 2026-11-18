"""Preserve legacy vectors and rebuild into the Qwen 4096-dimensional space."""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = '129aef843201'
down_revision = '08abca132100'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw')
    op.alter_column('knowledge_chunks', 'embedding', new_column_name='legacy_embedding', nullable=True)
    op.add_column('knowledge_chunks', sa.Column('embedding', Vector(4096), nullable=True))
    # A vector HNSW index cannot index 4096 dimensions; use exact search initially.
    op.execute("UPDATE knowledge_documents SET embedding_model=NULL, ingest_owner=NULL, "
               "status=CASE WHEN source_bytes IS NOT NULL THEN 'pending' ELSE 'failed' END, "
               "error_message=CASE WHEN source_bytes IS NULL THEN '模型已升级，请重新上传源文件' ELSE NULL END")


def downgrade():
    raise RuntimeError('模型空间迁移需要显式恢复旧向量，不能自动降级')
