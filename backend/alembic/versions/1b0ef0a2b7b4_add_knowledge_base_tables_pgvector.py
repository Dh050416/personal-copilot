"""add knowledge base tables (pgvector)

Revision ID: 1b0ef0a2b7b4
Revises: a4ef23e2f0b5
Create Date: 2026-11-10 20:01:02.752688

RAG 个人知识库数据层：
- 启用 pgvector 扩展（向量类型 + 相似度操作符）
- knowledge_documents：文档元数据，(user_id, content_hash) 联合唯一做内容去重
- knowledge_chunks：文本块 + bge-m3 1024 维向量，ON DELETE CASCADE 随文档级联删除
- embedding 字段建 HNSW 索引（vector_cosine_ops 余弦距离），加速 top-k 相似度检索
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '1b0ef0a2b7b4'
down_revision: Union[str, Sequence[str], None] = 'a4ef23e2f0b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector 扩展：提供 vector 类型与 <=> 余弦距离操作符
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    op.create_table(
        'knowledge_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=16), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('chunk_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'content_hash'),
    )
    op.create_index('ix_knowledge_documents_user_id', 'knowledge_documents', ['user_id'])

    op.create_table(
        'knowledge_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=False),
        sa.Column('embedding', Vector(1024), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['knowledge_documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_knowledge_chunks_document_id', 'knowledge_chunks', ['document_id'])
    op.create_index('ix_knowledge_chunks_user_id', 'knowledge_chunks', ['user_id'])

    # HNSW 索引：余弦距离（vector_cosine_ops）。个人知识库量级（几万块内）查询快、召回率高。
    op.execute(
        'CREATE INDEX ix_knowledge_chunks_embedding_hnsw '
        'ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)'
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw')
    op.drop_index('ix_knowledge_chunks_user_id', table_name='knowledge_chunks')
    op.drop_index('ix_knowledge_chunks_document_id', table_name='knowledge_chunks')
    op.drop_table('knowledge_chunks')
    op.drop_index('ix_knowledge_documents_user_id', table_name='knowledge_documents')
    op.drop_table('knowledge_documents')
    # vector 扩展不 drop：可能被其他表/库共用，保留无害。
