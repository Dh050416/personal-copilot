"""Import lifecycle, concurrent finalization and refund accounting on isolated PostgreSQL."""
import asyncio
import csv
import io
import json
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.bill_imports import router as imports_router
from app.api.bill_refunds import router as refunds_router
from app.api.reports import router as reports_router
from app.config import Settings
from app.data.models import Bill, BillCategoryRule, BillImportJob, BillImportRow, now_utc
from app.services.bill_imports import claim_job, confirm_job, enqueue, process_job
from app.services.bill_pipeline import stage_file
from app.services.classification import BillClassifier, ClassificationItem


def csv_file(*rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['微信支付账单明细'])
    writer.writerow(['交易时间', '交易类型', '交易对方', '商品', '收/支', '金额(元)', '当前状态', '交易单号'])
    for row in rows:
        writer.writerow(row)
    return out.getvalue().encode('utf-8-sig')


def row(tx='001', merchant='张某', product='收款方备注:二维码收款', kind='扫二维码付款',
        direction='支出', amount='10.00', status='支付成功'):
    return ['2026-11-01 08:00:00', kind, merchant, product, direction, amount, status, tx]


@pytest.fixture
async def client(db):
    app = FastAPI()
    app.state.db = db

    @app.middleware('http')
    async def user(request, call_next):
        request.state.user_id = int(request.headers.get('x-user', '1'))
        return await call_next(request)

    app.include_router(imports_router)
    app.include_router(refunds_router)
    app.include_router(reports_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        yield c


async def processed(db, content, monkeypatch, name='bill.csv', account='default'):
    async def predict(self, items):
        return [ClassificationItem(key=i['key'], category='购物', confidence=.95, reason='商品描述为牙刷')
                for i in items]
    monkeypatch.setattr(BillClassifier, 'predict_batch', predict)
    job = await enqueue(db, 1, name, content, account)
    claim = await claim_job(db)
    assert claim and claim[0] == job['id']
    await process_job(db, *claim, Settings(_env_file=None))
    return job['id']


def test_pandas_staging_refunds_conflicts_and_text_ids():
    data = csv_file(row(status='已退款(¥2.00)'), row(amount='11.00'),
                    row('002', kind='中铁网络-退款', direction='收入', amount='2.00'),
                    row('003', kind='转入零钱通-来自零钱', direction='/'))
    rows, errors, platform = stage_file(data, 'test.csv', 'account-a')
    assert platform == 'wechat' and not errors
    assert rows[0]['tx_no'] == '001' and rows[0]['refund_cents'] == 200
    assert rows[0]['conflict'] and rows[1]['conflict']
    assert rows[2]['nature'] == 'refund' and rows[3]['nature'] == 'internal'


async def test_upload_review_batch_rule_and_idempotent_confirmation(db, client, monkeypatch):
    content = csv_file(row(), row('002'))
    a, b = await asyncio.gather(enqueue(db, 1, 'bill.csv', content, 'default'),
                               enqueue(db, 1, 'bill.csv', content, 'default'))
    assert a['id'] == b['id']
    claim = await claim_job(db)
    assert await claim_job(db) is None
    await process_job(db, *claim, Settings(_env_file=None))  # no model call for generic personal names
    jid = a['id']
    detail = (await client.get(f'/api/bills/imports/{jid}')).json()
    assert detail['status'] == 'review' and detail['pending'] == 2 and len(detail['groups']) == 1
    assert (await client.get(f'/api/bills/imports/{jid}', headers={'x-user': '2'})).status_code == 404
    async with db.sessions() as session:
        assert await session.scalar(select(func.count(Bill.id))) == 0
    response = await client.post(f'/api/bills/imports/{jid}/rows', json={
        'row_ids': [1, 2], 'category': '餐饮', 'save_rule': True})
    assert response.status_code == 200
    first, second = await asyncio.gather(confirm_job(db, 1, jid, False), confirm_job(db, 1, jid, False))
    assert first['result']['inserted'] == second['result']['inserted'] == 2
    async with db.sessions() as session:
        assert await session.scalar(select(func.count(Bill.id))) == 2
        assert await session.scalar(select(func.count(BillCategoryRule.id))) == 1
    next_id = await processed(db, csv_file(row('003')), monkeypatch)
    records = (await client.get(f'/api/bills/imports/{next_id}/rows')).json()
    assert records[0]['data']['category_source'] == 'user_rule'
    assert records[0]['data']['category_status'] == 'confirmed'


async def test_invalid_rows_require_explicit_exclusion(db, client, monkeypatch):
    jid = await processed(db, csv_file(row(), row('002', amount='bad')), monkeypatch)
    response = await client.post(f'/api/bills/imports/{jid}/confirm', json={})
    assert response.status_code == 409
    response = await client.post(f'/api/bills/imports/{jid}/confirm', json={'exclude_errors': True})
    assert response.status_code == 200 and response.json()['result']['excluded'] == 1
    errors = await client.get(f'/api/bills/imports/{jid}/errors')
    assert errors.status_code == 200 and '金额不是有效数字' in errors.text


async def test_overlapping_files_preserve_human_category_update_refund(db, client, monkeypatch):
    first = await processed(db, csv_file(row()), monkeypatch)
    await client.post(f'/api/bills/imports/{first}/rows', json={'row_ids': [1], 'category': '餐饮'})
    await confirm_job(db, 1, first, False)
    second = await processed(db, csv_file(row(status='已全额退款')), monkeypatch)
    result = await confirm_job(db, 1, second, False)
    assert result['result']['inserted'] == 0
    async with db.sessions() as session:
        bill = await session.scalar(select(Bill))
        assert bill.category_source == 'user' and bill.refund_cents == 1000
    consumption = (await client.get('/api/reports/summary?mode=consumption')).json()
    raw = (await client.get('/api/reports/summary?mode=raw')).json()
    assert consumption['total_expense_cents'] == 0 and raw['total_expense_cents'] == 1000


async def test_refund_link_does_not_double_count_and_checks_ownership(db, client, monkeypatch):
    jid = await processed(db, csv_file(row(status='已退款¥2.00'),
                                     row('002', kind='商户消费-退款', direction='收入', amount='2.00')),
                          monkeypatch)
    await confirm_job(db, 1, jid, False)
    async with db.sessions() as session:
        original, refund = list(await session.scalars(select(Bill).order_by(Bill.id)))
        original_id, refund_id = original.id, refund.id
    path = f'/api/bills/{refund_id}/refund-link'
    assert (await client.post(path, json={'original_bill_id': original_id}, headers={'x-user': '2'})).status_code == 404
    assert (await client.post(path, json={'original_bill_id': original_id})).status_code == 200
    assert (await client.post(path, json={'original_bill_id': original_id})).status_code == 200
    data = (await client.get('/api/reports/summary?mode=consumption')).json()
    assert data['total_expense_cents'] == 800 and data['total_income_cents'] == 0
    from app.agent.tools import build_tools
    query = next(t for t in build_tools(db) if t.name == 'query_bill')
    message = await query.arun({'accounting_mode': 'consumption'}, tool_call_id='refund-query')
    assert message.artifact.total == '8.00' and message.artifact.items[0].amount == '10.00'
    assert (await client.delete(path)).status_code == 200


async def test_stale_owner_cannot_publish_and_retry_preserves_user_choice(db, client, monkeypatch):
    jid = await processed(db, csv_file(row()), monkeypatch)
    await client.post(f'/api/bills/imports/{jid}/rows', json={'row_ids': [1], 'category': '交通'})
    assert (await client.post(f'/api/bills/imports/{jid}/retry')).status_code == 200
    claim = await claim_job(db)
    async with db.sessions() as session, session.begin():
        job = await session.get(BillImportJob, jid)
        job.updated_at = now_utc() - timedelta(seconds=100)
    replacement = await claim_job(db)
    await process_job(db, *claim, Settings(_env_file=None))
    async with db.sessions() as session:
        job = await session.get(BillImportJob, jid)
        assert job.status == 'processing' and job.owner == replacement[1]
    await process_job(db, *replacement, Settings(_env_file=None))
    async with db.sessions() as session:
        staged = await session.get(BillImportRow, (jid, 1))
        assert json.loads(staged.data_json)['category'] == '交通'


async def test_model_failure_keeps_rows_reviewable(db, client, monkeypatch):
    async def fail(self, items):
        raise TimeoutError()
    monkeypatch.setattr(BillClassifier, 'predict_batch', fail)
    job = await enqueue(db, 1, 'b.csv', csv_file(row(product='电动牙刷')), 'default')
    claim = await claim_job(db)
    await process_job(db, *claim, Settings(_env_file=None))
    detail = (await client.get(f"/api/bills/imports/{job['id']}")).json()
    assert detail['status'] == 'review' and detail['result']['failed_batches'] == 1
    assert detail['pending'] == 1


async def test_legacy_migration_is_explicit_and_preserves_human_category(db, client, monkeypatch):
    parsed, _, _ = stage_file(csv_file(row()), 'b.csv', 'default')
    fields = {c.name for c in Bill.__table__.columns}
    old = {k: v for k, v in parsed[0].items() if k in fields}
    old.update(source='xlsx', category='餐饮', category_source='user')
    async with db.sessions() as session, session.begin():
        session.add(Bill(user_id=1, import_batch_id='legacy', **old))
    jid = await processed(db, csv_file(row()), monkeypatch)
    assert (await client.post(f'/api/bills/imports/{jid}/confirm', json={})).status_code == 409
    assert (await client.post(f'/api/bills/imports/{jid}/rows', json={
        'row_ids': [1], 'adopt_legacy': True})).status_code == 200
    await confirm_job(db, 1, jid, False)
    async with db.sessions() as session:
        bills = list(await session.scalars(select(Bill)))
        assert len(bills) == 1 and bills[0].source == 'wechat' and bills[0].category_source == 'user'
