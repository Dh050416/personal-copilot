"""写工具幂等测试：单卡（卡表 invocation_id 唯一约束）与批量（幂等记录表）同键不重复建卡。

覆盖：
- 服务层 create_card：同键重放首次结果 / 不同键正常新建 / 无键 uuid 兼容
- 工具层：ContextVar 注入的键被工具读取（单卡 + 批量）
- 图级端到端：approve 派生 approval_id → run_tools 注入 → 同 call id 二次执行命中查重
"""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from app.agent.graph import initial_input, make_graph
from app.agent.tools import _IDEMPOTENCY_KEY, WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import TrackingCard
from app.schemas import CreateTrackingCardArgs
from app.services.card_service import create_card


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'idem.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        import json

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk
        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        chunk_kwargs = {'content': msg.content or ''}
        if getattr(msg, 'tool_calls', None):
            chunk_kwargs['tool_call_chunks'] = [
                {'name': tc['name'], 'args': json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        yield ChatGenerationChunk(message=AIMessageChunk(**chunk_kwargs))


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


async def run_with_approvals(graph, graph_input, config):
    """循环段式执行：input_checkpoint 自动放行；tool_approval 自动批准（同 id 回带）。"""
    current = graph_input
    while True:
        result = await graph.ainvoke(current, config)
        resume = None
        for inter in result.get('__interrupt__', []):
            value = getattr(inter, 'value', inter)
            if isinstance(value, dict) and value.get('kind') == 'input_checkpoint':
                resume = {'new_input': None}
                break
            if isinstance(value, dict) and value.get('kind') == 'tool_approval':
                resume = {'decision': 'approve', 'approval_id': value['approval_id']}
                break
        if resume is None:
            return result
        current = Command(resume=resume)


async def _invoke(tool_obj, args):
    msg = await tool_obj.arun(args, tool_call_id='call-test')
    return msg.content, msg.artifact


async def card_count(db):
    async with db.sessions() as session:
        return await session.scalar(select(func.count(TrackingCard.id)))


async def seed_bills(db):
    from app.data.models import Bill

    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no='a', tx_time='2026-09-01T10:00:00', merchant='张三', product='转账',
                     amount_cents=10000, direction='expense', transaction_type='转账',
                     payment_method='零钱', transaction_status='已转账', merchant_tx_no='ma', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=1),
                Bill(tx_no='b', tx_time='2026-09-01T11:00:00', merchant='李四', product='转账',
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


# ---------- 服务层：单卡幂等 ----------

@pytest.mark.asyncio
async def test_create_card_same_key_replays_first_result(ctx):
    """同键重复调用：只建一张卡，第二次返回首次的同一张卡（重放）。"""
    args = CreateTrackingCardArgs(category='餐饮', purpose='报销')
    v1 = await create_card(ctx['db'], args, idempotency_key='apr-call-1')
    v2 = await create_card(ctx['db'], args, idempotency_key='apr-call-1')
    assert v1['id'] == v2['id']
    assert await card_count(ctx['db']) == 1


@pytest.mark.asyncio
async def test_create_card_different_keys_create_separate_cards(ctx):
    """不同键（不同操作意图）→ 正常各自建卡，互不干扰。"""
    args = CreateTrackingCardArgs(category='餐饮', purpose='报销')
    v1 = await create_card(ctx['db'], args, idempotency_key='apr-call-1')
    v2 = await create_card(ctx['db'], args, idempotency_key='apr-call-2')
    assert v1['id'] != v2['id']
    assert await card_count(ctx['db']) == 2


@pytest.mark.asyncio
async def test_create_card_without_key_uses_random_uuid(ctx):
    """无键路径（历史兼容）：每次随机 uuid，正常新建。"""
    args = CreateTrackingCardArgs(category='餐饮', purpose='报销')
    v1 = await create_card(ctx['db'], args)
    v2 = await create_card(ctx['db'], args)
    assert v1['id'] != v2['id']
    assert await card_count(ctx['db']) == 2


# ---------- 工具层：ContextVar 键注入 ----------

@pytest.mark.asyncio
async def test_tool_single_card_same_key_not_duplicated(ctx):
    """工具层同键两次（模拟超时重试）：键经 ContextVar 注入，第二次查重命中。"""
    tool_obj = next(t for t in ctx['tools'] if t.name == 'create_tracking_card')
    token = _IDEMPOTENCY_KEY.set('apr-call-x')
    try:
        c1, v1 = await _invoke(tool_obj, {'category': '餐饮', 'purpose': '报销'})
        c2, v2 = await _invoke(tool_obj, {'category': '餐饮', 'purpose': '报销'})
    finally:
        _IDEMPOTENCY_KEY.reset(token)
    assert v1.id == v2.id
    assert '卡片 ID' in c1 and '卡片 ID' in c2
    assert await card_count(ctx['db']) == 1
    async with ctx['db'].sessions() as session:
        card = await session.scalar(select(TrackingCard).where(TrackingCard.invocation_id == 'apr-call-x'))
        assert card is not None


@pytest.mark.asyncio
async def test_tool_batch_same_key_replays_batch(ctx):
    """批量建卡同键两次：卡数不翻倍，第二次返回与首次完全相同的批次结果。"""
    await seed_bills(ctx['db'])
    tool_obj = next(t for t in ctx['tools'] if t.name == 'create_cards_from_query')
    args = {'card_category': '餐饮', 'purpose': '报销'}
    token = _IDEMPOTENCY_KEY.set('apr-batch-1')
    try:
        c1, b1 = await _invoke(tool_obj, args)
        c2, b2 = await _invoke(tool_obj, args)
    finally:
        _IDEMPOTENCY_KEY.reset(token)
    assert len(b1.items) == 3
    assert b2.card_ids == b1.card_ids  # 重放：与首次完全一致
    assert '批量创建 3 张' in c1 and '批量创建 3 张' in c2
    assert await card_count(ctx['db']) == 3  # 没有翻倍


@pytest.mark.asyncio
async def test_tool_batch_different_keys_duplicate_expected(ctx):
    """批量不同键（两次不同的操作意图）→ 正常翻倍建卡。"""
    await seed_bills(ctx['db'])
    tool_obj = next(t for t in ctx['tools'] if t.name == 'create_cards_from_query')
    args = {'card_category': '餐饮', 'purpose': '报销'}
    token = _IDEMPOTENCY_KEY.set('apr-batch-1')
    try:
        _, b1 = await _invoke(tool_obj, args)
    finally:
        _IDEMPOTENCY_KEY.reset(token)
    token = _IDEMPOTENCY_KEY.set('apr-batch-2')
    try:
        _, b2 = await _invoke(tool_obj, args)
    finally:
        _IDEMPOTENCY_KEY.reset(token)
    assert len(b2.items) == 3
    assert await card_count(ctx['db']) == 6


# ---------- 图级端到端：approval_id → run_tools 注入 → 重试安全 ----------

@pytest.mark.asyncio
async def test_graph_retry_same_call_id_creates_one_card(ctx):
    """端到端：模型两次输出同 id 的建卡调用（模拟响应丢失后的重试）——
    两次都经审批，第二次 run_tools 按 apr-call-c1 查重命中，只建一张卡。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-c1'),
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-c1'),
        AIMessage(content='卡片已创建。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    result = await run_with_approvals(graph, initial_input('idem-t', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'idem-t'}})
    assert await card_count(ctx['db']) == 1
    results = [e['payload']['result'] for e in result['events'] if e['type'] == 'tool_result']
    assert len(results) == 2
    assert results[0]['ok'] is True and results[1]['ok'] is True
    assert results[0]['data']['id'] == results[1]['data']['id']  # 第二次重放同一张卡
    assert result['final'] == '卡片已创建。'


@pytest.mark.asyncio
async def test_graph_rejected_call_does_not_create_card(ctx):
    """拒绝审批 → 不执行、不建卡（幂等键无副作用路径不受影响）。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r1'),
        AIMessage(content='好的，不建卡。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())

    async def run_reject(graph, graph_input, config):
        current = graph_input
        while True:
            result = await graph.ainvoke(current, config)
            resume = None
            for inter in result.get('__interrupt__', []):
                value = getattr(inter, 'value', inter)
                if isinstance(value, dict) and value.get('kind') == 'input_checkpoint':
                    resume = {'new_input': None}
                    break
                if isinstance(value, dict) and value.get('kind') == 'tool_approval':
                    resume = {'decision': 'reject', 'approval_id': value['approval_id']}
                    break
            if resume is None:
                return result
            current = Command(resume=resume)

    result = await run_reject(graph, initial_input('idem-r', '建一张餐饮卡'),
                              {'configurable': {'thread_id': 'idem-r'}})
    assert await card_count(ctx['db']) == 0
    assert '拒绝' in result['final'] or result['final'] == '好的，不建卡。'
