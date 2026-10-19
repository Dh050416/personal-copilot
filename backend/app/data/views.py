"""ORM 对象 -> 对外视图（保持前端契约的字段与格式）。"""
from app.data.models import Bill, ScheduleEvent, TrackingCard


def money(cents):
    from decimal import Decimal
    return format(Decimal(cents) / Decimal(100), '.2f')


def bill_view(bill: Bill):
    return {'id': bill.id, 'tx_no': bill.tx_no, 'tx_time': bill.tx_time,
            'merchant': bill.merchant, 'amount': money(bill.amount_cents),
            'direction': bill.direction, 'transaction_type': bill.transaction_type,
            'product': bill.product, 'payment_method': bill.payment_method,
            'transaction_status': bill.transaction_status, 'note': bill.note,
            'category_source': bill.category_source, 'category_confidence': bill.category_confidence,
            'category': bill.category, 'source': bill.source}


def card_view(card: TrackingCard):
    return {'id': card.id, 'bill_id': card.bill_id, 'category': card.category,
            'purpose': card.purpose, 'note': card.note, 'status': card.status,
            'direction': card.direction, 'transaction_type': card.transaction_type,
            'counterparty': card.counterparty,
            'amount': money(card.amount_cents) if card.amount_cents is not None else None,
            'occurred_at': card.occurred_at}


def event_view(event: ScheduleEvent):
    return {'id': event.id, 'title': event.title, 'start_at': event.start_at,
            'end_at': event.end_at, 'location': event.location,
            'note': event.note, 'status': event.status}
