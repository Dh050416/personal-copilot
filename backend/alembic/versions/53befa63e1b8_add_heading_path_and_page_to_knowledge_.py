"""add heading_path and page to knowledge_chunks

Revision ID: 53befa63e1b8
Revises: 1b0ef0a2b7b4
Create Date: 2026-11-10 22:36:55.468822

补第二层遗漏：chunks 表加 heading_path（章节路径，检索溯源用）+ page（页码）。
两列均可空（旧数据无结构信息），新摄入的文档由 ingest.py 写入。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '53befa63e1b8'
down_revision: Union[str, Sequence[str], None] = '1b0ef0a2b7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_chunks',
                  sa.Column('heading_path', sa.String(length=512), nullable=True))
    op.add_column('knowledge_chunks',
                  sa.Column('page', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_chunks', 'page')
    op.drop_column('knowledge_chunks', 'heading_path')
