"""账单查询数据等价测试（LangChain 工具直调）。"""
import pytest

from app.agent.tools import build_tools
from app.data.db import Database
from app.data.models import Bill


@pytest.fixture
async def tools(tmp_path):
    db = Database(tmp_path / 'queries.db')
    await db.initialize()
    tool_map = {t.name: t for t in build_tools(db)}
    yield {'db': db, 'tools': tool_map}
    await db.close()


async def seed_query_bills(db):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no='a', tx_time='2026-09-01T10:00:00', merchant='张三', product='转账',
                     amount_cents=10000, direction='expense', transaction_type='转账',
                     payment_method='零钱', transaction_status='已转账', merchant_tx_no='ma', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=1),
                Bill(tx_no='b', tx_time='2026-09-01T11:00:00', merchant='张三', product='转账',
                     amount_cents=2500, direction='income', transaction_type='转账',
                     payment_method='银行卡', transaction_status='对方已收钱', merchant_tx_no='mb', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=2),
                Bill(tx_no='c', tx_time='2026-09-01T12:00:00', merchant='咖啡店', product='咖啡',
                     amount_cents=3500, direction='expense', transaction_type='商户消费',
                     payment_method='零钱', transaction_status='支付成功', merchant_tx_no='mc', note='',
                     category='餐饮', category_source='rule', category_confidence=.95,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=3),
            ])


async def invoke(tool, args):
    msg = await tool.arun(args, tool_call_id="call-test")
    return msg.content, msg.artifact


@pytest.mark.asyncio
async def test_query_answers_total_and_no_match(tools):
    await seed_query_bills(tools['db'])
    _, result = await invoke(tools['tools']['query_bill'], {'start_date': '2026-09-01', 'end_date': '2026-09-01'})
    assert result.count == 2
    assert result.total == '135.00'
    empty, _ = await invoke(tools['tools']['query_bill'], {'merchant': '不存在的人'})
    assert '没有匹配记录' in empty or '共 0 笔' in empty


@pytest.mark.asyncio
async def test_query_returns_content_text_for_model(tools):
    await seed_query_bills(tools['db'])
    content, result = await invoke(tools['tools']['query_bill'], {'category': '餐饮'})
    assert '共 1 笔' in content and '35.00' in content
    assert result.total == '35.00'
