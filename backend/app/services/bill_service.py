"""Import/enrichment and classification persistence. Inference precedes writes."""
import json
from uuid import uuid4

from sqlalchemy import select, update

from app.data.models import Bill, ImportBatch, now_iso
from app.services.classification import PROTECTED_SOURCES

CLASS_FIELDS = ('category', 'category_source', 'category_confidence')
RAW_FIELDS = ('transaction_type', 'payment_method', 'transaction_status', 'merchant_tx_no', 'note', 'product')
CORE_FIELDS = ('tx_time', 'amount_cents', 'direction', 'merchant')


def is_missing(value):
    return value in (None, '', '/', '未知')


async def import_rows(db, rows, filename, classifier):
    unique = {}
    for incoming in rows:
        key = (incoming['source'], incoming['tx_no'])
        if key in unique and any(unique[key][k] != incoming[k] for k in CORE_FIELDS + RAW_FIELDS):
            raise ValueError('文件内同一交易单号内容冲突，请核对原始账单')
        unique.setdefault(key, dict(incoming))
    async with db.sessions() as session:
        for row in unique.values():
            existing = await session.scalar(select(Bill).where(Bill.source == row['source'], Bill.tx_no == row['tx_no']))
            if existing:
                if any(getattr(existing, key) != row[key] for key in CORE_FIELDS):
                    raise ValueError('已有同一交易单号的日期、金额、收支或对方不一致，未覆盖已有记录')
                if existing.category_source in PROTECTED_SOURCES | {'rule', 'llm'}:
                    row.update({key: getattr(existing, key) for key in CLASS_FIELDS})
    report = await classifier.classify(list(unique.values()))
    batch_id = uuid4().hex
    inserted, enriched = 0, 0
    async with db.sessions() as session:
        async with session.begin():
            session.add(ImportBatch(id=batch_id, filename=filename,
                                    report_json=json.dumps({'rows': list(unique.values()), 'classification': report},
                                                           ensure_ascii=False)))
            for row in unique.values():
                existing = await session.scalar(select(Bill).where(Bill.source == row['source'], Bill.tx_no == row['tx_no']))
                if existing is None:
                    session.add(Bill(**row, import_batch_id=batch_id, category_checked_at=now_iso()))
                    inserted += 1
                    continue
                if any(getattr(existing, key) != row[key] for key in CORE_FIELDS):
                    raise ValueError('交易在导入期间发生变化，请重新核对')
                changed = False
                for key in RAW_FIELDS:
                    if is_missing(getattr(existing, key)) and not is_missing(row[key]):
                        setattr(existing, key, row[key])
                        changed = True
                if existing.category_source not in PROTECTED_SOURCES | {'rule', 'llm'}:
                    for key in CLASS_FIELDS:
                        changed = changed or getattr(existing, key) != row[key]
                        setattr(existing, key, row[key])
                    existing.category_checked_at = now_iso()
                enriched += int(changed)
    return {'status': 'imported', 'batch_id': batch_id, 'inserted': inserted, 'enriched': enriched,
            'skipped': len(rows) - inserted, 'errors': [], 'classification': report}


async def classify_existing(db, classifier, limit):
    async with db.sessions() as session:
        bills = list(await session.scalars(select(Bill).where(
            Bill.category_source.in_(['auto', 'unclassified'])
        ).order_by(Bill.category_checked_at, Bill.id.desc()).limit(limit)))
        rows = [{key: getattr(b, key) for key in (
            'id', 'merchant', 'product', 'transaction_type', 'direction', *CLASS_FIELDS
        )} for b in bills]
    report = await classifier.classify(rows)
    updated = 0
    async with db.sessions() as session:
        async with session.begin():
            for row in rows:
                # A concurrent human confirmation always wins over model inference.
                result = await session.execute(update(Bill).where(
                    Bill.id == row['id'], Bill.category_source.in_(['auto', 'unclassified'])
                ).values(**{k: row[k] for k in CLASS_FIELDS}, category_checked_at=now_iso()))
                updated += result.rowcount if row['category_source'] in {'rule', 'llm'} else 0
    return {'processed': len(rows), 'classified': updated, **report}
