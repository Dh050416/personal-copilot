"""Durable resumable knowledge uploads."""
from alembic import op
import sqlalchemy as sa

revision = '34a120ef0011'
down_revision = '239abf001201'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('knowledge_uploads',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True))
    op.create_index('ix_knowledge_uploads_user_id', 'knowledge_uploads', ['user_id'])
    op.create_index('ix_knowledge_uploads_expires_at', 'knowledge_uploads', ['expires_at'])
    op.create_table('knowledge_upload_parts',
        sa.Column('upload_id', sa.String(32), sa.ForeignKey('knowledge_uploads.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('part', sa.Integer(), primary_key=True),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False))


def downgrade():
    op.drop_table('knowledge_upload_parts')
    op.drop_table('knowledge_uploads')
