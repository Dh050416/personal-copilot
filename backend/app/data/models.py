from datetime import datetime, timezone

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户账号：每个用户一个独立的生活管家（数据按 user_id 隔离）。"""
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(default=now_iso)


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
    category_checked_at: Mapped[str] = mapped_column(String(40), default='')
    source: Mapped[str] = mapped_column(String(32))
    import_batch_id: Mapped[str] = mapped_column(String(64))
    source_sheet: Mapped[str] = mapped_column(String(128))
    source_row: Mapped[int]


class ImportBatch(Base):
    __tablename__ = 'import_batches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(default=now_iso)
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
    created_at: Mapped[str] = mapped_column(default=now_iso)


class AgentMemory(Base):
    """长期记忆：用户明确表达的事实/偏好（key-value，跨会话持久，全量注入 system）。
    按 user_id 隔离：每个用户有自己的一份记忆。"""
    __tablename__ = 'agent_memory'
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)


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
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


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
    created_at: Mapped[str] = mapped_column(default=now_iso)
