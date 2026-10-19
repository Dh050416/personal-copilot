"""跟踪卡片业务层：创建时的快照规则与持久化。

快照规则（与前端看板契约对齐）：显式传参优先；未指定时若关联账单，
自动从该账单复制（对方=商户/转账对方、时间=交易时间、金额=账单金额、方向=账单方向）。
幂等：create_card 支持 idempotency_key（approval_id），同键重复调用重放首次结果，不重复建卡。
"""
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.data.models import Bill, TrackingCard
from app.data.views import card_view
from app.errors import ToolError
from app.schemas import CardView


async def create_card(db, args, idempotency_key: str | None = None) -> CardView:
    """创建跟踪卡片并返回对外视图；关联账单不存在时抛业务错误。

    idempotency_key 非空时启用幂等：先按 invocation_id 查重，命中即重放首次结果；
    未命中则新建（invocation_id=key）。并发同键由唯一约束兜底（IntegrityError → 重查返回）。
    """
    async with db.sessions() as session:
        async with session.begin():
            # 查重在事务内：避免会话先 autobegin 再显式 begin() 冲突
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(TrackingCard).where(TrackingCard.invocation_id == idempotency_key))
                if existing is not None:
                    return card_view(existing)
            bill = None
            if args.bill_id is not None:
                bill = await session.get(Bill, args.bill_id)
                if bill is None:
                    raise ToolError('BILL_NOT_FOUND', '关联账单不存在，请先查询真实账单 ID',
                                     stage='business', retryable=False)
            direction = args.direction or (bill.direction if bill else 'expense')
            transaction_type = args.transaction_type or (bill.transaction_type if bill else '其他')
            counterparty = args.counterparty or (bill.merchant if bill else '')
            occurred_at = args.occurred_at or (bill.tx_time if bill else '')
            amount_cents = int(args.amount * 100) if args.amount is not None else None
            if amount_cents is None and bill is not None:
                amount_cents = bill.amount_cents
            card = TrackingCard(
                bill_id=args.bill_id, category=args.category, purpose=args.purpose,
                note=args.note, direction=direction, transaction_type=transaction_type,
                counterparty=counterparty, amount_cents=amount_cents, occurred_at=occurred_at,
                invocation_id=idempotency_key or uuid4().hex,
            )
            session.add(card)
            try:
                await session.flush()
            except IntegrityError:
                # 并发同键：另一请求已建卡，重查返回首次结果
                existing = await session.scalar(
                    select(TrackingCard).where(TrackingCard.invocation_id == idempotency_key))
                if existing is not None:
                    return card_view(existing)
                raise
            return card_view(card)


async def complete_card(db, card_id: int) -> CardView:
    """将跟踪卡标记为已完成（移入看板「已完成」区）。
    幂等：已完成的卡片重复操作直接返回当前视图，不产生副作用。
    """
    async with db.sessions() as session:
        async with session.begin():
            card = await session.get(TrackingCard, card_id)
            if card is None:
                raise ToolError('CARD_NOT_FOUND', '卡片不存在，请先查询真实卡片 ID',
                                stage='business', retryable=False)
            if card.status != 'done':
                card.status = 'done'
            return card_view(card)
