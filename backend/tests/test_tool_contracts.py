"""LangChain @tool 工具层契约：结构化输出、schema 校验、业务错误、治理标记。"""
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agent.tools import TOOL_PERMISSIONS, WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import Bill, TrackingCard
from app.errors import ToolError
from app.schemas import CardList, CardView, QueryBillResult


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'tools.db')
    await db.initialize()
    yield {'db': db, 'tools': {t.name: t for t in build_tools(db)}}
    await db.close()


async def invoke(tool, args):
    msg = await tool.arun(args, tool_call_id="call-test")
    return msg.content, msg.artifact


async def seed_bills(db):
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


def test_tool_description_comes_from_docstring(ctx):
    assert '查询历史账单' in ctx['tools']['query_bill'].description
    assert '创建待办卡片' in ctx['tools']['create_tracking_card'].description
    assert '事务卡片' in ctx['tools']['list_tracking_cards'].description


def test_write_tool_governance_markers():
    assert WRITE_TOOLS == frozenset(
        {'create_tracking_card', 'complete_tracking_card', 'create_cards_from_query',
         'create_event', 'complete_event', 'update_event'})
    assert TOOL_PERMISSIONS == {
        'create_tracking_card': ('billing:card:create',),
        'complete_tracking_card': ('billing:card:complete',),
        'create_cards_from_query': ('billing:card:create',),
        'create_event': ('schedule:event:create',),
        'complete_event': ('schedule:event:complete',),
        'update_event': ('schedule:event:update',),
    }


@pytest.mark.asyncio
async def test_query_bill_returns_structured_artifact(ctx):
    await seed_bills(ctx['db'])
    content, result = await invoke(ctx['tools']['query_bill'], {'category': '餐饮'})
    assert isinstance(result, QueryBillResult)
    assert result.count == 1 and result.total == '35.00'
    assert result.returned_count == 1 and result.truncated is False
    assert result.items[0].merchant == '咖啡店'
    assert result.filters['category'] == '餐饮'
    assert '共 1 笔' in content and '35.00' in content


@pytest.mark.asyncio
async def test_query_filters_transfer_counterparty_and_amount(ctx):
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['query_bill'], {
        'start_date': '2026-09-01', 'end_date': '2026-09-01', 'merchant': '张三',
        'transaction_type': '转账', 'direction': 'expense', 'min_amount': '100.00',
    })
    assert result.count == 1 and result.total == '100.00'
    assert result.items[0].payment_method == '零钱'


@pytest.mark.parametrize('args,expected,total', [
    ({'merchant': '张三', 'direction': 'income'}, ['b'], '25.00'),
    ({'merchant': '张三', 'direction': None}, ['b', 'a'], '125.00'),
    ({'merchant': '张三', 'direction': 'neutral'}, ['b', 'a'], '125.00'),
    ({'min_amount': '100', 'min_amount_inclusive': False}, [], '0.00'),
    ({'min_amount': '100'}, ['a'], '100.00'),
    ({'max_amount': '35', 'max_amount_inclusive': False}, [], '0.00'),
    ({'min_amount': '35', 'max_amount': '35'}, ['c'], '35.00'),
    ({'transaction_status': '收钱', 'direction': None}, ['b'], '25.00'),
    ({'payment_method': '银行卡', 'direction': None}, ['b'], '25.00'),
    ({'payment_method': '零钱', 'min_amount': '50'}, ['a'], '100.00'),
    ({'merchant': '%'}, [], '0.00'),
    ({'merchant': '_'}, [], '0.00'),
    ({'merchant': "' OR 1=1 --"}, [], '0.00'),
    ({'merchant': '张', 'merchant_match': 'exact'}, [], '0.00'),
    ({'merchant': '张'}, ['a'], '100.00'),
])
@pytest.mark.asyncio
async def test_sql_filter_boundaries(ctx, args, expected, total):
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['query_bill'], args)
    assert [r.tx_no for r in result.items] == expected
    assert result.total == total


@pytest.mark.parametrize('args', [
    {'min_amount': '1.001'}, {'max_amount': '-1'}, {'min_amount': 'NaN'}, {'min_amount': 'Infinity'},
    {'min_amount': 2, 'max_amount': 1},
    {'min_amount': 1, 'max_amount': 1, 'min_amount_inclusive': False},
    {'max_amount_inclusive': False}, {'merchant': '   '}, {'limit': 0}, {'limit': 101},
])
@pytest.mark.asyncio
async def test_invalid_filter_arguments_are_rejected(ctx, args):
    with pytest.raises(ValidationError):
        await invoke(ctx['tools']['query_bill'], args)


@pytest.mark.asyncio
async def test_query_uses_full_sum_not_limited_details(ctx):
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['query_bill'], {'limit': 1, 'direction': None})
    assert result.returned_count == 1 and result.count == 3
    assert result.truncated is True
    assert result.total == '160.00'


@pytest.mark.asyncio
async def test_missing_legacy_fields_are_disclosed(ctx):
    await seed_bills(ctx['db'])
    async with ctx['db'].sessions() as session:
        row = await session.get(Bill, 1)
        row.payment_method = '未知'
        await session.commit()
    _, result = await invoke(ctx['tools']['query_bill'], {'merchant': '张三', 'payment_method': '零钱'})
    assert result.count == 0
    assert any('重新导入' in w for w in result.warnings)


@pytest.mark.asyncio
async def test_create_card_returns_card_view_and_persists(ctx):
    await seed_bills(ctx['db'])
    content, card = await invoke(ctx['tools']['create_tracking_card'], 
        {'bill_id': 3, 'category': '餐饮', 'purpose': '报销'})
    assert isinstance(card, CardView)
    assert card.bill_id == 3 and card.category == '餐饮' and card.purpose == '报销'
    # 快照：账单 c = 咖啡店 · 商户消费 · expense · 3500 分 · 2026-09-01T12:00:00
    assert card.direction == 'expense' and card.transaction_type == '商户消费'
    assert card.counterparty == '咖啡店' and card.amount == '35.00'
    assert card.occurred_at == '2026-09-01T12:00:00'
    assert '已创建' in content and '-¥35.00' in content and '收款人：咖啡店' in content
    async with ctx['db'].sessions() as session:
        cards = list(await session.scalars(select(TrackingCard)))
        assert len(cards) == 1 and cards[0].bill_id == 3
        assert cards[0].amount_cents == 3500 and cards[0].counterparty == '咖啡店'


@pytest.mark.asyncio
async def test_create_card_snapshots_income_bill(ctx):
    await seed_bills(ctx['db'])
    _, card = await invoke(ctx['tools']['create_tracking_card'], 
        {'bill_id': 2, 'category': '其他', 'purpose': 'AA'})
    assert card.direction == 'income' and card.transaction_type == '转账'
    assert card.counterparty == '张三' and card.amount == '25.00'
    assert card.occurred_at == '2026-09-01T11:00:00'


@pytest.mark.asyncio
async def test_create_card_explicit_args_override_snapshot(ctx):
    await seed_bills(ctx['db'])
    _, card = await invoke(ctx['tools']['create_tracking_card'], {
        'bill_id': 3, 'category': '餐饮', 'purpose': '报销',
        'direction': 'income', 'transaction_type': '二维码',
        'counterparty': '李四', 'amount': '50.00', 'occurred_at': '2026-09-02T08:00:00',
    })
    assert card.direction == 'income' and card.transaction_type == '二维码'
    assert card.counterparty == '李四' and card.amount == '50.00'
    assert card.occurred_at == '2026-09-02T08:00:00'


@pytest.mark.asyncio
async def test_create_card_without_bill_allows_empty_snapshot(ctx):
    _, card = await invoke(ctx['tools']['create_tracking_card'],
        {'category': '订阅', 'purpose': '待退订'})
    assert card.bill_id is None and card.amount is None
    assert card.direction == 'expense' and card.transaction_type == '其他'
    assert card.counterparty == '' and card.occurred_at == ''


@pytest.mark.asyncio
async def test_create_card_missing_bill_is_business_error(ctx):
    with pytest.raises(ToolError) as exc:
        await invoke(ctx['tools']['create_tracking_card'], 
            {'bill_id': 999, 'category': '餐饮', 'purpose': '报销'})
    assert exc.value.code == 'BILL_NOT_FOUND'
    assert exc.value.stage == 'business' and exc.value.retryable is False


@pytest.mark.asyncio
async def test_list_cards_returns_card_list(ctx):
    await seed_bills(ctx['db'])
    await invoke(ctx['tools']['create_tracking_card'], {'category': '订阅', 'purpose': '待退订'})
    content, cards = await invoke(ctx['tools']['list_tracking_cards'], {})
    assert isinstance(cards, CardList)
    assert len(cards.items) == 1
    assert cards.items[0].purpose == '待退订'
    assert '#1' in content and '订阅' in content


@pytest.mark.asyncio
async def test_complete_card_marks_done_and_is_idempotent(ctx):
    await seed_bills(ctx['db'])
    _, card = await invoke(ctx['tools']['create_tracking_card'],
        {'bill_id': 3, 'category': '餐饮', 'purpose': '报销'})
    content, done = await invoke(ctx['tools']['complete_tracking_card'], {'card_id': card.id})
    assert isinstance(done, CardView)
    assert done.status == 'done' and done.id == card.id
    assert '已完成' in content and f'卡片 ID：{card.id}' in content
    # 幂等：重复完成仍返回成功
    _, again = await invoke(ctx['tools']['complete_tracking_card'], {'card_id': card.id})
    assert again.status == 'done'
    async with ctx['db'].sessions() as session:
        stored = await session.get(TrackingCard, card.id)
        assert stored.status == 'done'


@pytest.mark.asyncio
async def test_complete_card_missing_is_business_error(ctx):
    with pytest.raises(ToolError) as exc:
        await invoke(ctx['tools']['complete_tracking_card'], {'card_id': 999})
    assert exc.value.code == 'CARD_NOT_FOUND'
    assert exc.value.stage == 'business' and exc.value.retryable is False


@pytest.mark.asyncio
async def test_complete_card_rejects_invalid_id(ctx):
    with pytest.raises(ValidationError):
        await invoke(ctx['tools']['complete_tracking_card'], {})
    with pytest.raises(ValidationError):
        await invoke(ctx['tools']['complete_tracking_card'], {'card_id': 0})


# ---------- create_cards_from_query 批量建卡 ----------

async def seed_many_bills(db, n=10):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no=f'm{i}', tx_time=f'2026-08-{i % 28 + 1:02d}T10:00:00',
                     merchant=f'收款人{i:03d}', product='转账', amount_cents=10000 + i * 100,
                     direction='expense', transaction_type='转账', payment_method='零钱',
                     transaction_status='已转账', merchant_tx_no=f'mm{i}', note='',
                     category='其他', category_source='rule', category_confidence=.95,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=i + 1)
                for i in range(n)
            ])


@pytest.mark.asyncio
async def test_batch_create_cards_for_all_matching(ctx):
    await seed_many_bills(ctx['db'], n=10)
    content, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'start_date': '2026-08-01', 'end_date': '2026-08-31',
        'transaction_type': '转账', 'direction': 'expense',
        'card_category': '其他', 'purpose': '报销',
    })
    assert result.total == 10 and len(result.card_ids) == 10
    assert result.total_amount == format(sum(100 + i for i in range(10)), '.2f')
    assert len(result.items) == 10
    # 快照：对方/金额/方向/时间从账单复制，分类/用途为传入目标
    first = result.items[0]
    assert first.counterparty == '收款人000' and first.amount == '100.00'
    assert first.direction == 'expense' and first.transaction_type == '转账'
    assert first.category == '其他' and first.purpose == '报销'
    assert '批量创建 10 张' in content
    async with ctx['db'].sessions() as session:
        cards = list(await session.scalars(select(TrackingCard)))
        assert len(cards) == 10


@pytest.mark.asyncio
async def test_batch_create_honors_filters_and_limit(ctx):
    await seed_many_bills(ctx['db'], n=10)
    _, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'start_date': '2026-08-01', 'end_date': '2026-08-31',
        'transaction_type': '转账', 'direction': 'expense',
        'min_amount': '108.00', 'max_amount': '109.00',
        'card_category': '餐饮', 'purpose': 'AA', 'limit': 5,
    })
    assert result.total == 2
    assert all(i.category == '餐饮' and i.purpose == 'AA' for i in result.items)
    assert result.items[0].counterparty == '收款人008'


@pytest.mark.asyncio
async def test_batch_create_no_match_returns_empty(ctx):
    await seed_many_bills(ctx['db'], n=3)
    content, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'start_date': '2026-08-01', 'end_date': '2026-08-31',
        'merchant': '不存在的商户', 'card_category': '其他',
    })
    assert result.total == 0 and result.card_ids == []
    assert result.total_amount == '0.00'
    assert '没有匹配的账单' in content


@pytest.mark.asyncio
async def test_batch_create_rejects_invalid_args(ctx):
    await seed_many_bills(ctx['db'], n=3)
    with pytest.raises(ValidationError):  # 日期倒序
        await invoke(ctx['tools']['create_cards_from_query'], {
            'start_date': '2026-08-31', 'end_date': '2026-08-01', 'card_category': '其他'})
    with pytest.raises(ValidationError):  # limit 超上限
        await invoke(ctx['tools']['create_cards_from_query'], {
            'limit': 501, 'card_category': '其他'})
    with pytest.raises(ValidationError):  # 无分类也无规则
        await invoke(ctx['tools']['create_cards_from_query'], {})


@pytest.mark.asyncio
async def test_batch_create_rules_assign_per_card_category(ctx):
    """category_rules：不同记录分到不同分类；顺序命中第一条；未命中回退 card_category。"""
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'transaction_type': '转账', 'direction': None,
        'category_rules': [
            {'merchant': '张三', 'direction': 'expense', 'category': '餐饮'},
            {'merchant': '张三', 'direction': 'income', 'category': '购物'},
        ],
        'card_category': '其他',
    })
    assert result.total == 2
    # 顺序按 tx_time asc：a(张三·expense)→餐饮；b(张三·income)→购物
    assert [i.category for i in result.items] == ['餐饮', '购物']
    assert [i.counterparty for i in result.items] == ['张三', '张三']


@pytest.mark.asyncio
async def test_batch_create_rules_fall_back_to_default(ctx):
    """未命中任何规则的记录回退 card_category。"""
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'direction': None,
        'category_rules': [{'merchant': '张三', 'category': '餐饮'}],
        'card_category': '交通',
    })
    assert result.total == 3
    cats = {i.counterparty: i.category for i in result.items}
    assert cats['张三'] == '餐饮' and cats['咖啡店'] == '交通'


@pytest.mark.asyncio
async def test_batch_create_use_bill_category_follows_bill(ctx):
    """use_bill_category：卡片分类直接跟随账单已有分类（餐饮→餐饮泳道）。"""
    await seed_bills(ctx['db'])
    _, result = await invoke(ctx['tools']['create_cards_from_query'], {
        'direction': None, 'use_bill_category': True,
    })
    assert result.total == 3
    cats = {i.counterparty: i.category for i in result.items}
    # a(张三·其他) b(张三·其他) c(咖啡店·餐饮)
    assert cats['张三'] == '其他' and cats['咖啡店'] == '餐饮'
