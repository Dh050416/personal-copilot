from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.data.models import Bill, BillRefundLink

router = APIRouter(prefix='/api/bills', tags=['bill refunds'])


class LinkRefund(BaseModel):
    original_bill_id: int = Field(gt=0)


@router.post('/{bill_id}/refund-link')
async def link_refund(bill_id: int, payload: LinkRefund, request: Request):
    async with request.app.state.db.sessions() as session, session.begin():
        # Stable lock ordering prevents two concurrent links from deadlocking.
        bills = {b.id: b for b in await session.scalars(select(Bill).where(
            Bill.id.in_([bill_id, payload.original_bill_id]), Bill.user_id == request.state.user_id
        ).order_by(Bill.id).with_for_update())}
        refund, original = bills.get(bill_id), bills.get(payload.original_bill_id)
        if not refund or not original:
            raise HTTPException(404, '账单不存在')
        if refund.nature != 'refund' or original.nature != 'consumption':
            raise HTTPException(400, '请选择退款记录和对应的消费记录')
        if (refund.account, refund.source) != (original.account, original.source):
            raise HTTPException(400, '退款与原交易必须来自同一平台及账户')
        existing = await session.get(BillRefundLink, bill_id)
        if existing:
            if existing.original_bill_id != original.id:
                raise HTTPException(409, '该退款已关联其他交易，请先解除关联')
            return {'linked': True}
        total = await session.scalar(select(func.coalesce(func.sum(BillRefundLink.amount_cents), 0)).where(
            BillRefundLink.original_bill_id == original.id))
        if total + refund.amount_cents > original.amount_cents:
            raise HTTPException(409, '关联退款总额不能超过原交易金额')
        session.add(BillRefundLink(refund_bill_id=bill_id, original_bill_id=original.id,
                                   amount_cents=refund.amount_cents, basis='用户明确确认原交易'))
        return {'linked': True}


@router.delete('/{bill_id}/refund-link')
async def unlink_refund(bill_id: int, request: Request):
    async with request.app.state.db.sessions() as session, session.begin():
        refund = await session.scalar(select(Bill).where(Bill.id == bill_id,
            Bill.user_id == request.state.user_id).with_for_update())
        if not refund:
            raise HTTPException(404, '账单不存在')
        link = await session.get(BillRefundLink, bill_id)
        if link:
            await session.delete(link)
    return {'linked': False}
