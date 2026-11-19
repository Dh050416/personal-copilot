"""Shared SQL expressions for explicit, non-duplicating refund accounting."""
from sqlalchemy import case, func, select

from app.data.models import Bill, BillRefundLink


def effective_consumption():
    linked = select(func.coalesce(func.sum(BillRefundLink.amount_cents), 0)).where(
        BillRefundLink.original_bill_id == Bill.id).correlate(Bill).scalar_subquery()
    # Status and separate refund rows can describe the same refund. Never add both.
    refund = func.least(Bill.amount_cents, func.greatest(Bill.refund_cents, linked))
    return case(((Bill.direction == 'expense') & (Bill.nature == 'consumption'),
                 Bill.amount_cents - refund), else_=0)
