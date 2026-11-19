"""Manual bill creation shared by REST and approved Agent tools."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from app.data.models import Bill, now_utc
from app.data.views import bill_view


async def add_bill(db, user_id, args, idempotency_key: str | None = None):
    """随手记：手动录入一笔账单（source='manual'，不经过文件导入）。

    幂等：idempotency_key（= approval_id）非空时，用 tx_no 承载该键，
    命中同 tx_no 记录即重放首次结果，避免重试/重复提交产生第二笔账单。
    """
    if idempotency_key is not None:
        tx_no = idempotency_key
    else:
        tx_no = f'manual-{uuid4().hex}'
    # 用本地时间（而非 UTC）记录交易时间：账单按「本地日期」归档与统计，
    # UTC 会因 GMT+8 时区差导致跨天/跨月错位（尤其凌晨记录会落到前一天）。
    occurred_at = args.occurred_at or datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    async with db.sessions() as session:
        async with session.begin():
            existing = await session.scalar(
                select(Bill).where(Bill.user_id == user_id, Bill.source == 'manual', Bill.tx_no == tx_no))
            if existing is not None:
                return bill_view(existing)
            bill = Bill(
                user_id=user_id,
                tx_no=tx_no,
                tx_time=occurred_at,
                merchant=args.merchant or '',
                product='',
                amount_cents=int(args.amount * 100),
                direction=args.direction,
                transaction_type=args.transaction_type,
                payment_method='手动录入',
                transaction_status='手动录入',
                merchant_tx_no='',
                note=args.note or '',
                category=args.category,
                category_source='manual',
                category_confidence=1.0,
                category_checked_at=now_utc(),
                source='manual',
                import_batch_id='manual',
                source_sheet='',
                source_row=0,
                nature='consumption' if args.direction == 'expense' else 'unknown',
                category_status='confirmed', category_reason='用户手动录入',
            )
            session.add(bill)
            await session.flush()
            return bill_view(bill)
