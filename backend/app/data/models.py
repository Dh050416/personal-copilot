from datetime import date, datetime, timezone
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    """系统时间戳默认值：带时区的 UTC 当前时间。"""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """业务/事件序列化用的 ISO 字符串时间戳（graph 事件、审批 payload 等）。
    数据库系统时间戳字段已改用 DateTime(timezone=True)，此函数仅服务于非入库场景。"""
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class AgentInput(Base):
    __tablename__ = 'agent_inputs'
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(32), unique=True)
    thread_id: Mapped[str] = mapped_column(Text, index=True)
    content: Mapped[str] = mapped_column(Text)


class AgentDecision(Base):
    __tablename__ = 'agent_decisions'
    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    decision: Mapped[str] = mapped_column(Text)


class KnowledgeUpload(Base):
    __tablename__ = 'knowledge_uploads'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class KnowledgeUploadPart(Base):
    __tablename__ = 'knowledge_upload_parts'
    upload_id: Mapped[str] = mapped_column(ForeignKey('knowledge_uploads.id', ondelete='CASCADE'), primary_key=True)
    part: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_hash: Mapped[str] = mapped_column(String(64))


class User(Base):
    """用户账号：每个用户一个独立的生活管家（数据按 user_id 隔离）。"""
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Bill(Base):
    __tablename__ = 'bills'
    __table_args__ = (UniqueConstraint('user_id', 'source', 'account', 'tx_no', name='uq_bill_identity'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    tx_no: Mapped[str] = mapped_column(String(128))
    tx_time: Mapped[str] = mapped_column(String(32), index=True)
    merchant: Mapped[str] = mapped_column(String(255))
    product: Mapped[str] = mapped_column(String(500), default='')
    # 金额以「分」存整数，避免浮点误差；BigInteger 兼容 PG 与 SQLite，留足扩展空间。
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(16))
    transaction_type: Mapped[str] = mapped_column(String(64), default='未知')
    payment_method: Mapped[str] = mapped_column(String(64), default='未知')
    transaction_status: Mapped[str] = mapped_column(String(64), default='未知')
    merchant_tx_no: Mapped[str] = mapped_column(String(128), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    category: Mapped[str] = mapped_column(String(64), default='其他')
    category_source: Mapped[str] = mapped_column(String(32), default='auto')
    category_confidence: Mapped[float] = mapped_column(default=0.0)
    category_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    import_batch_id: Mapped[str] = mapped_column(String(64))
    source_sheet: Mapped[str] = mapped_column(String(128))
    source_row: Mapped[int]
    account: Mapped[str] = mapped_column(String(80), default='default', server_default='default')
    nature: Mapped[str] = mapped_column(String(32), default='unknown', server_default='unknown')
    category_status: Mapped[str] = mapped_column(String(24), default='pending', server_default='pending')
    category_reason: Mapped[str] = mapped_column(Text, default='', server_default='')
    refund_cents: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')


class ImportBatch(Base):
    __tablename__ = 'import_batches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    report_json: Mapped[str] = mapped_column(Text)


class BillImportJob(Base):
    __tablename__ = 'bill_import_jobs'
    __table_args__ = (UniqueConstraint('user_id', 'account', 'file_hash', name='uq_bill_import_file'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    account: Mapped[str] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(32), default='')
    error: Mapped[str] = mapped_column(Text, default='')
    result_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class BillImportRow(Base):
    __tablename__ = 'bill_import_rows'
    job_id: Mapped[str] = mapped_column(ForeignKey('bill_import_jobs.id', ondelete='CASCADE'), primary_key=True)
    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_json: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(Text, default='')
    disposition: Mapped[str] = mapped_column(String(24), default='new')
    excluded: Mapped[bool] = mapped_column(default=False)


class BillCategoryRule(Base):
    __tablename__ = 'bill_category_rules'
    __table_args__ = (UniqueConstraint('user_id', 'platform', 'account', 'merchant', 'product',
                                     'transaction_type', name='uq_bill_category_rule'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    platform: Mapped[str] = mapped_column(String(32))
    account: Mapped[str] = mapped_column(String(80))
    merchant: Mapped[str] = mapped_column(String(255))
    product: Mapped[str] = mapped_column(String(500))
    transaction_type: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64))


class BillRefundLink(Base):
    __tablename__ = 'bill_refund_links'
    refund_bill_id: Mapped[int] = mapped_column(ForeignKey('bills.id'), primary_key=True)
    original_bill_id: Mapped[int] = mapped_column(ForeignKey('bills.id'), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    basis: Mapped[str] = mapped_column(Text)


class TrackingCard(Base):
    __tablename__ = 'tracking_cards'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    bill_id: Mapped[int | None] = mapped_column(ForeignKey('bills.id'), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(64))
    # 卡片属性（建卡时从账单快照带入，也可手动指定）
    direction: Mapped[str] = mapped_column(String(16), default='expense')
    transaction_type: Mapped[str] = mapped_column(String(64), default='其他')
    counterparty: Mapped[str] = mapped_column(String(255), default='')
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String(32), default='')
    status: Mapped[str] = mapped_column(default='active')
    note: Mapped[str] = mapped_column(Text, default='')
    # 泳道（看板栏目）：用户明确说「放在 XX 栏目」时原样写入；NULL = 按分类/用途推导
    lane: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    invocation_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AgentMemory(Base):
    """长期记忆：用户明确表达的事实/偏好（key-value，跨会话持久，全量注入 system）。
    按 user_id 隔离：每个用户有自己的一份记忆。"""
    __tablename__ = 'agent_memory'
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class IdempotencyRecord(Base):
    """写工具幂等记录：键 = 一次确认过的操作意图（approval_id，由 tool_call id 派生、checkpoint 稳定）。

    命中记录时重放首次执行的响应（content + artifact），保证重试/重复提交不产生第二次副作用。
    单卡工具直接复用 tracking_cards.invocation_id 唯一约束查重；批量工具（一次建 N 张卡）
    无法共用单卡键，用本表以「批次键 → 首次响应」存证。按 user_id 隔离。"""
    __tablename__ = 'idempotency_records'
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    content: Mapped[str] = mapped_column(Text, default='')
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScheduleEvent(Base):
    """日程事件：标题/起止时间/地点/备注/状态。支撑「查日程、建日程、完成、改期」四类操作，
    写操作（建/完成/改期）与卡片一致走 HITL 审批与幂等（invocation_id=approval_id）。"""
    __tablename__ = 'schedule_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    start_at: Mapped[str] = mapped_column(String(32), index=True)
    end_at: Mapped[str] = mapped_column(String(32), default='')
    location: Mapped[str] = mapped_column(String(255), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(default='active')  # active | done
    invocation_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ConversationSummary(Base):
    """对话摘要（递进摘要/滑动窗口的持久化载体）。

    当 thread 的原始消息超过窗口阈值时，把最旧的一批「普通文本消息」合并进
    summary_text（递进摘要：旧摘要 + 增量消息 → 新摘要），避免每轮重算全量历史。
    工具调用 / 审批 / 系统事件等结构化消息不参与摘要（否则破坏幂等与恢复语义）。
    按 thread_id 隔离；user_id 冗余便于将来按用户清理。"""
    __tablename__ = 'conversation_summaries'
    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    summary_text: Mapped[str] = mapped_column(Text, default='')
    last_summarized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeDocument(Base):
    """RAG 知识库文档元数据：一次上传/粘贴对应一条记录。

    - 原始文件持久化为 source_bytes，支持重启恢复与重新摄入。
    - content_hash（SHA-256）做内容级去重：同一用户重复上传同一内容直接拒绝，
      跨用户互不影响（联合唯一约束含 user_id）。
    - status 跟踪摄入状态：pending（切块/向量化中）→ ready / failed；
      摄入失败只留 failed 记录 + error_message，不产生"半个文档"的脏块。
    - title 是用户可编辑的展示标题；filename 记录原始文件名（溯源用，不可改）。
    """
    __tablename__ = 'knowledge_documents'
    source_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingest_owner: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ingest_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'content_hash'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(16))  # file | paste
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='pending')  # pending | ready | failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class KnowledgeChunk(Base):
    """文档分块 + bge-m3 向量（1024 维），RAG 检索的最小单位。

    - content 是块的原始文本：向量只是索引，检索命中后要把 content 喂给模型。
    - user_id 冗余自文档，检索时直接按用户过滤，不必 join documents。
    - embedding 用 pgvector 的 Vector 类型；HNSW 索引（余弦距离）在 Alembic 迁移里建。
    - 删文档时 ON DELETE CASCADE 连带删所有块（真删，无回收站）。
    """
    __tablename__ = 'knowledge_chunks'
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey('knowledge_documents.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    # 章节路径（解析时保留的结构信息，检索命中后连同路径喂给模型，避免数字脱离上下文）
    heading_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[Vector] = mapped_column(Vector(4096))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class _PersonalOwned:
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))


class _PersonalTimestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class PersonalGoal(_PersonalOwned, _PersonalTimestamps, Base):
    __tablename__ = 'personal_goals'
    __table_args__ = (
        UniqueConstraint('user_id', 'id', name='uq_personal_goal_owner'),
        Index('ix_personal_goal_owner_updated', 'user_id', 'updated_at'),
        CheckConstraint("progress_style IN ('water','route','plant')", name='ck_personal_goal_style'),
        CheckConstraint(
            "(progress_mode = 'metric' AND unit IS NOT NULL AND target_value IS NOT NULL "
            "AND target_value > 0 AND current_value IS NOT NULL AND current_value >= 0 AND manual_percent IS NULL) "
            "OR (progress_mode = 'manual' AND manual_percent IS NOT NULL AND manual_percent BETWEEN 0 AND 100 "
            "AND unit IS NULL AND target_value IS NULL AND current_value IS NULL) "
            "OR (progress_mode = 'milestone' AND unit IS NULL AND target_value IS NULL "
            "AND current_value IS NULL AND manual_percent IS NULL)",
            name='ck_personal_goal_progress_fields',
        ),
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    success_criteria: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[date | None]
    progress_mode: Mapped[str] = mapped_column(String(16))
    progress_style: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(32))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    manual_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)


class PersonalGoalMilestone(_PersonalOwned, _PersonalTimestamps, Base):
    __tablename__ = 'personal_goal_milestones'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'goal_id'], ['personal_goals.user_id', 'personal_goals.id']),
        UniqueConstraint('user_id', 'goal_id', 'id', name='uq_personal_milestone_owner'),
        Index('ix_personal_milestone_goal_position', 'user_id', 'goal_id', 'position'),
    )
    goal_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255))
    position: Mapped[int]
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalGoalProgressEntry(_PersonalOwned, Base):
    __tablename__ = 'personal_goal_progress_entries'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'goal_id'], ['personal_goals.user_id', 'personal_goals.id']),
        ForeignKeyConstraint(
            ['user_id', 'goal_id', 'milestone_id'],
            ['personal_goal_milestones.user_id', 'personal_goal_milestones.goal_id', 'personal_goal_milestones.id'],
        ),
        UniqueConstraint('user_id', 'goal_id', 'request_key', name='uq_personal_progress_request'),
        Index('ix_personal_progress_goal_time', 'user_id', 'goal_id', 'occurred_at'),
        Index('ix_personal_progress_owner_time', 'user_id', 'occurred_at'),
        CheckConstraint(
            "entry_type IN ('initial','metric','manual','milestone','target_change','mode_change')",
            name='ck_personal_progress_type',
        ),
    )
    goal_id: Mapped[int] = mapped_column(BigInteger)
    milestone_id: Mapped[int | None] = mapped_column(BigInteger)
    entry_type: Mapped[str] = mapped_column(String(24))
    before_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    after_data: Mapped[dict] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PersonalReview(_PersonalOwned, _PersonalTimestamps, Base):
    __tablename__ = 'personal_reviews'
    __table_args__ = (
        UniqueConstraint('user_id', 'id', name='uq_personal_review_owner'),
        UniqueConstraint('user_id', 'period_kind', 'period_start', name='uq_personal_review_period'),
        CheckConstraint("period_kind IN ('week','month')", name='ck_personal_review_kind'),
        CheckConstraint("status IN ('draft','saved')", name='ck_personal_review_status'),
        CheckConstraint('period_start < period_end', name='ck_personal_review_period'),
    )
    period_kind: Mapped[str] = mapped_column(String(8))
    period_start: Mapped[date]
    period_end: Mapped[date]
    timezone: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    user_notes: Mapped[str] = mapped_column(Text)
    source_snapshot: Mapped[dict] = mapped_column(JSONB)
    ai_draft: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    final_text: Mapped[str | None] = mapped_column(Text)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)


class PersonalTask(_PersonalOwned, _PersonalTimestamps, Base):
    __tablename__ = 'personal_tasks'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'goal_id'], ['personal_goals.user_id', 'personal_goals.id']),
        ForeignKeyConstraint(['user_id', 'origin_review_id'], ['personal_reviews.user_id', 'personal_reviews.id']),
        UniqueConstraint('user_id', 'id', name='uq_personal_task_owner'),
        UniqueConstraint('user_id', 'origin_review_id', 'origin_action_key', name='uq_personal_task_review_action'),
        Index('ix_personal_task_owner_status_updated', 'user_id', 'board_status', 'updated_at'),
        Index('ix_personal_task_owner_due', 'user_id', 'due_at'),
        CheckConstraint("board_status IN ('inbox','todo','doing','done')", name='ck_personal_task_status'),
        CheckConstraint("(board_status = 'done') = (completed_at IS NOT NULL)", name='ck_personal_task_completion'),
        CheckConstraint(
            "NOT is_waiting OR (board_status IN ('todo','doing') AND archived_at IS NULL)",
            name='ck_personal_task_waiting',
        ),
        CheckConstraint(
            '(origin_review_id IS NULL) = (origin_action_key IS NULL)', name='ck_personal_task_review_action',
        ),
    )
    goal_id: Mapped[int | None] = mapped_column(BigInteger)
    origin_review_id: Mapped[int | None] = mapped_column(BigInteger)
    origin_action_key: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    board_status: Mapped[str] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    is_waiting: Mapped[bool]
    waiting_note: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)


class PersonalTaskCheckItem(_PersonalOwned, _PersonalTimestamps, Base):
    __tablename__ = 'personal_task_check_items'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['personal_tasks.user_id', 'personal_tasks.id']),
        Index('ix_personal_check_item_task_position', 'user_id', 'task_id', 'position'),
    )
    task_id: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str] = mapped_column(String(255))
    position: Mapped[int]
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalTaskEvent(_PersonalOwned, Base):
    __tablename__ = 'personal_task_events'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['personal_tasks.user_id', 'personal_tasks.id']),
        Index('ix_personal_task_event_owner_time', 'user_id', 'occurred_at'),
        CheckConstraint(
            "event_type IN ('created','state_changed','due_changed','archived','restored')",
            name='ck_personal_task_event_type',
        ),
    )
    task_id: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(24))
    before_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    after_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PersonalTaskReminder(_PersonalOwned, Base):
    __tablename__ = 'personal_task_reminders'
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['personal_tasks.user_id', 'personal_tasks.id']),
        CheckConstraint("kind IN ('start','follow_up')", name='ck_personal_reminder_kind'),
        CheckConstraint("state IN ('scheduled','available','cancelled')", name='ck_personal_reminder_state'),
        Index('ix_personal_reminder_owner_state_fire', 'user_id', 'state', 'fire_at'),
        Index('ix_personal_reminder_task', 'user_id', 'task_id', 'kind', 'id'),
        Index(
            'uq_personal_reminder_scheduled', 'user_id', 'task_id', 'kind',
            unique=True, postgresql_where=text("state = 'scheduled'"),
        ),
    )
    task_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

