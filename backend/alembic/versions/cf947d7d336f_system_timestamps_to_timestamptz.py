"""system timestamps to timestamptz

Revision ID: cf947d7d336f
Revises: abfa67df9ade
Create Date: 2026-11-10 15:12:32.953948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'cf947d7d336f'
down_revision: Union[str, Sequence[str], None] = 'abfa67df9ade'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 系统时间戳 VARCHAR -> timestamptz。原值为 ISO 字符串或空串，
    # 用 NULLIF(col,'') 把空串转 NULL 再 cast，避免空串无法 cast 成 timestamptz。
    op.alter_column('agent_memory', 'updated_at',
                    existing_type=sa.VARCHAR(length=40),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(updated_at, '')::timestamptz")
    op.alter_column('bills', 'category_checked_at',
                    existing_type=sa.VARCHAR(length=40),
                    type_=sa.DateTime(timezone=True),
                    nullable=True,
                    postgresql_using="NULLIF(category_checked_at, '')::timestamptz")
    op.alter_column('idempotency_records', 'created_at',
                    existing_type=sa.VARCHAR(length=40),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(created_at, '')::timestamptz")
    op.alter_column('import_batches', 'created_at',
                    existing_type=sa.VARCHAR(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(created_at, '')::timestamptz")
    op.alter_column('schedule_events', 'created_at',
                    existing_type=sa.VARCHAR(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(created_at, '')::timestamptz")
    op.alter_column('tracking_cards', 'created_at',
                    existing_type=sa.VARCHAR(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(created_at, '')::timestamptz")
    op.alter_column('users', 'created_at',
                    existing_type=sa.VARCHAR(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False,
                    postgresql_using="NULLIF(created_at, '')::timestamptz")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('users', 'created_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(),
                    existing_nullable=False,
                    postgresql_using='created_at::text')
    op.alter_column('tracking_cards', 'created_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(),
                    existing_nullable=False,
                    postgresql_using='created_at::text')
    op.alter_column('schedule_events', 'created_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(),
                    existing_nullable=False,
                    postgresql_using='created_at::text')
    op.alter_column('import_batches', 'created_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(),
                    existing_nullable=False,
                    postgresql_using='created_at::text')
    op.alter_column('idempotency_records', 'created_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(length=40),
                    existing_nullable=False,
                    postgresql_using='created_at::text')
    op.alter_column('bills', 'category_checked_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(length=40),
                    nullable=False,
                    postgresql_using="COALESCE(category_checked_at::text, '')")
    op.alter_column('agent_memory', 'updated_at',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.VARCHAR(length=40),
                    existing_nullable=False,
                    postgresql_using='updated_at::text')
