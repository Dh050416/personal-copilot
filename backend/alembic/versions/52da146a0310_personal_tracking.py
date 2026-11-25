"""Add personal tasks, goals, reminders and period reviews."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '52da146a0310'
down_revision = '45b230ab0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'personal_goals',
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('success_criteria', sa.Text(), nullable=True),
        sa.Column('target_date', sa.Date(), nullable=True),
        sa.Column('progress_mode', sa.String(length=16), nullable=False),
        sa.Column('progress_style', sa.String(length=16), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=True),
        sa.Column('target_value', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('current_value', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('manual_percent', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(progress_mode = 'metric' AND unit IS NOT NULL AND target_value IS NOT NULL AND target_value > 0 AND current_value IS NOT NULL AND current_value >= 0 AND manual_percent IS NULL) OR (progress_mode = 'manual' AND manual_percent IS NOT NULL AND manual_percent BETWEEN 0 AND 100 AND unit IS NULL AND target_value IS NULL AND current_value IS NULL) OR (progress_mode = 'milestone' AND unit IS NULL AND target_value IS NULL AND current_value IS NULL AND manual_percent IS NULL)",
            name='ck_personal_goal_progress_fields',
        ),
        sa.CheckConstraint("progress_style IN ('water','route','plant')", name='ck_personal_goal_style'),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'id', name='uq_personal_goal_owner'),
    )
    op.create_index(
        'ix_personal_goal_owner_updated', 'personal_goals', ['user_id', 'updated_at'], unique=False
    )
    op.create_table(
        'personal_reviews',
        sa.Column('period_kind', sa.String(length=8), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('user_notes', sa.Text(), nullable=False),
        sa.Column('source_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('ai_draft', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column('final_text', sa.Text(), nullable=True),
        sa.Column('saved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period_kind IN ('week','month')", name='ck_personal_review_kind'),
        sa.CheckConstraint("status IN ('draft','saved')", name='ck_personal_review_status'),
        sa.CheckConstraint('period_start < period_end', name='ck_personal_review_period'),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'id', name='uq_personal_review_owner'),
        sa.UniqueConstraint('user_id', 'period_kind', 'period_start', name='uq_personal_review_period'),
    )
    op.create_table(
        'personal_goal_milestones',
        sa.Column('goal_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id', 'goal_id'],
            ['personal_goals.user_id', 'personal_goals.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'goal_id', 'id', name='uq_personal_milestone_owner'),
    )
    op.create_index(
        'ix_personal_milestone_goal_position',
        'personal_goal_milestones',
        ['user_id', 'goal_id', 'position'],
        unique=False,
    )
    op.create_table(
        'personal_tasks',
        sa.Column('goal_id', sa.BigInteger(), nullable=True),
        sa.Column('origin_review_id', sa.BigInteger(), nullable=True),
        sa.Column('origin_action_key', sa.String(length=64), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('board_status', sa.String(length=16), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('source', sa.String(length=128), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('is_waiting', sa.Boolean(), nullable=False),
        sa.Column('waiting_note', sa.Text(), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(board_status = 'done') = (completed_at IS NOT NULL)", name='ck_personal_task_completion'
        ),
        sa.CheckConstraint(
            "NOT is_waiting OR (board_status IN ('todo','doing') AND archived_at IS NULL)",
            name='ck_personal_task_waiting',
        ),
        sa.CheckConstraint("board_status IN ('inbox','todo','doing','done')", name='ck_personal_task_status'),
        sa.CheckConstraint(
            '(origin_review_id IS NULL) = (origin_action_key IS NULL)', name='ck_personal_task_review_action'
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'goal_id'],
            ['personal_goals.user_id', 'personal_goals.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'origin_review_id'],
            ['personal_reviews.user_id', 'personal_reviews.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'id', name='uq_personal_task_owner'),
        sa.UniqueConstraint(
            'user_id', 'origin_review_id', 'origin_action_key', name='uq_personal_task_review_action'
        ),
    )
    op.create_index('ix_personal_task_owner_due', 'personal_tasks', ['user_id', 'due_at'], unique=False)
    op.create_index(
        'ix_personal_task_owner_status_updated',
        'personal_tasks',
        ['user_id', 'board_status', 'updated_at'],
        unique=False,
    )
    op.create_table(
        'personal_goal_progress_entries',
        sa.Column('goal_id', sa.BigInteger(), nullable=False),
        sa.Column('milestone_id', sa.BigInteger(), nullable=True),
        sa.Column('entry_type', sa.String(length=24), nullable=False),
        sa.Column('before_data', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column('after_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('request_key', sa.String(length=64), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('initial','metric','manual','milestone','target_change','mode_change')",
            name='ck_personal_progress_type',
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'goal_id', 'milestone_id'],
            [
                'personal_goal_milestones.user_id',
                'personal_goal_milestones.goal_id',
                'personal_goal_milestones.id',
            ],
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'goal_id'],
            ['personal_goals.user_id', 'personal_goals.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'goal_id', 'request_key', name='uq_personal_progress_request'),
    )
    op.create_index(
        'ix_personal_progress_goal_time',
        'personal_goal_progress_entries',
        ['user_id', 'goal_id', 'occurred_at'],
        unique=False,
    )
    op.create_index(
        'ix_personal_progress_owner_time',
        'personal_goal_progress_entries',
        ['user_id', 'occurred_at'],
        unique=False,
    )
    op.create_table(
        'personal_task_check_items',
        sa.Column('task_id', sa.BigInteger(), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id', 'task_id'],
            ['personal_tasks.user_id', 'personal_tasks.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_personal_check_item_task_position',
        'personal_task_check_items',
        ['user_id', 'task_id', 'position'],
        unique=False,
    )
    op.create_table(
        'personal_task_events',
        sa.Column('task_id', sa.BigInteger(), nullable=False),
        sa.Column('event_type', sa.String(length=24), nullable=False),
        sa.Column('before_data', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column('after_data', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('created','state_changed','due_changed','archived','restored')",
            name='ck_personal_task_event_type',
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'task_id'],
            ['personal_tasks.user_id', 'personal_tasks.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_personal_task_event_owner_time', 'personal_task_events', ['user_id', 'occurred_at'], unique=False
    )
    op.create_table(
        'personal_task_reminders',
        sa.Column('task_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('fire_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('start','follow_up')", name='ck_personal_reminder_kind'),
        sa.CheckConstraint(
            "state IN ('scheduled','available','cancelled')", name='ck_personal_reminder_state'
        ),
        sa.ForeignKeyConstraint(
            ['user_id', 'task_id'],
            ['personal_tasks.user_id', 'personal_tasks.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_personal_reminder_owner_state_fire',
        'personal_task_reminders',
        ['user_id', 'state', 'fire_at'],
        unique=False,
    )
    op.create_index(
        'ix_personal_reminder_task',
        'personal_task_reminders',
        ['user_id', 'task_id', 'kind', 'id'],
        unique=False,
    )
    op.create_index(
        'uq_personal_reminder_scheduled',
        'personal_task_reminders',
        ['user_id', 'task_id', 'kind'],
        unique=True,
        postgresql_where=sa.text("state = 'scheduled'"),
    )


def downgrade() -> None:
    op.drop_table('personal_task_reminders')
    op.drop_table('personal_task_events')
    op.drop_table('personal_task_check_items')
    op.drop_table('personal_goal_progress_entries')
    op.drop_table('personal_tasks')
    op.drop_table('personal_goal_milestones')
    op.drop_table('personal_reviews')
    op.drop_table('personal_goals')
