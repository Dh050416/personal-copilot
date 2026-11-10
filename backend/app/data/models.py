from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class User(Base):
    """用户账号：每个用户一个独立的生活管家（数据按 user_id 隔离）。"""
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Bill(Base):
    __tablename__ = 'bills'
    __table_args__ = (UniqueConstraint('user_id', 'source', 'tx_no'),)
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


class ImportBatch(Base):
    __tablename__ = 'import_batches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    report_json: Mapped[str] = mapped_column(Text)


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

    - 原始文件不落库，只保留解析后的文本块（见 KnowledgeChunk）。
    - content_hash（SHA-256）做内容级去重：同一用户重复上传同一内容直接拒绝，
      跨用户互不影响（联合唯一约束含 user_id）。
    - status 跟踪摄入状态：pending（切块/向量化中）→ ready / failed；
      摄入失败只留 failed 记录 + error_message，不产生"半个文档"的脏块。
    - title 是用户可编辑的展示标题；filename 记录原始文件名（溯源用，不可改）。
    """
    __tablename__ = 'knowledge_documents'
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
    embedding: Mapped[Vector] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

