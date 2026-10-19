from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.data.db import Database
from app.data.models import Bill
from app.services.bill_service import classify_existing, import_rows
from app.services.classification import BillClassifier, ClassificationItem
from app.services.importer import parse_rows


@pytest.fixture
async def db(tmp_path):
    db = Database(tmp_path / 'imports.db')
    await db.initialize()
    yield db
    await db.close()


def import_fixture():
    rows, errors = parse_rows([
        ['交易单号', '交易时间', '交易对方', '商品', '收/支', '金额(元)', '交易类型', '支付方式', '当前状态', '商户单号', '备注'],
        ['fixture-1', '2026-09-01 10:00:00', '咖啡店', '拿铁', '支出', '12.50', '商户消费', '零钱', '支付成功', 'shop-1', '原始备注'],
    ], 'Sheet1', 'xlsx')
    assert not errors
    return rows


async def test_reimport_fills_raw_fields_preserves_human_category_and_identity(db):
    classifier = BillClassifier(Settings(_env_file=None))
    rows = import_fixture()
    first = await import_rows(db, rows, 'test.xlsx', classifier)
    assert first['inserted'] == 1
    async with db.sessions() as session:
        b = await session.scalar(select(Bill))
        bill_id = b.id
        b.payment_method = '未知'
        b.transaction_status = '未知'
        b.category = '住房'
        b.category_source = 'user'
        await session.commit()
    second = await import_rows(db, rows, 'test.xlsx', classifier)
    assert second['inserted'] == 0 and second['enriched'] == 1
    async with db.sessions() as session:
        assert await session.scalar(select(func.count(Bill.id))) == 1
        b = await session.get(Bill, bill_id)
        assert (b.category, b.category_source, b.payment_method, b.transaction_status) == ('住房', 'user', '零钱', '支付成功')
        assert b.merchant_tx_no == 'shop-1' and b.note == '原始备注'


async def test_duplicate_conflicts_do_not_overwrite(db):
    classifier = BillClassifier(Settings(_env_file=None))
    rows = import_fixture()
    report = await import_rows(db, rows * 2, 'test.xlsx', classifier)
    assert report['inserted'] == 1 and report['skipped'] == 1
    rows[0]['amount_cents'] = 999
    with pytest.raises(ValueError):
        await import_rows(db, rows, 'test.xlsx', classifier)
    async with db.sessions() as session:
        assert (await session.scalar(select(Bill))).amount_cents == 1250


async def test_concurrent_human_confirmation_wins(db, monkeypatch):
    classifier = BillClassifier(Settings(_env_file=None))
    await import_rows(db, import_fixture(), 'test.xlsx', classifier)
    async with db.sessions() as session:
        b = await session.scalar(select(Bill))
        b.category_source = 'unclassified'
        b.category = '其他'
        b.merchant = '未知商户'
        b.product = '未知商品'
        bill_id = b.id
        await session.commit()
    async def predict(items):
        async with db.sessions() as session:
            b = await session.get(Bill, bill_id)
            b.category = '教育'
            b.category_source = 'user'
            await session.commit()
        return [ClassificationItem(key=0, category='购物', confidence=1)]
    monkeypatch.setattr(classifier, 'predict_batch', AsyncMock(side_effect=predict))
    report = await classify_existing(db, classifier, 200)
    assert report['classified'] == 0
    async with db.sessions() as session:
        assert (await session.get(Bill, bill_id)).category == '教育'
