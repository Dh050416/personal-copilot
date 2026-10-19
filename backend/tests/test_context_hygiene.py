"""上下文卫生测试：工具结果截断 / 行数提炼 / 查询分页 / 结果缓存（TTL + 命中语义）。

对应实现：
- graph.py sanitize_tool_content + ToolResultCache + run_tools 卫生/缓存分支
- tools.py query_bill.offset 翻页、list_tracking_cards 分页（ListTrackingCardsArgs）
- schemas.py QueryBillArgs.offset / ListTrackingCardsArgs
"""
import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.graph import ToolResultCache, initial_input, make_graph
from app.agent.tools import CACHEABLE_TOOLS, WRITE_TOOLS, build_tools
from app.data.db import Database
from app.schemas import EmptyArgs


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'hygiene.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
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


async def run_to_end(graph, graph_input, config):
    """循环段式执行：input_checkpoint 自动 resume(None) 放行，直到 END；其它 interrupt 停住返回。"""
    current = graph_input
    while True:
        result = await graph.ainvoke(current, config)
        auto = False
        for inter in result.get('__interrupt__', []):
            value = getattr(inter, 'value', inter)
            if isinstance(value, dict) and value.get('kind') == 'input_checkpoint':
                auto = True
                break
        if not auto:
            return result
        current = Command(resume={'new_input': None})


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


async def seed_cards(db, n=5):
    from app.data.models import TrackingCard

    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                TrackingCard(bill_id=None, category='其他', purpose='报销', note=f'卡{i}',
                             direction='expense', transaction_type='其他', counterparty='',
                             amount_cents=1000 + i, occurred_at='', invocation_id=f'iv{i}')
                for i in range(n)
            ])


# ---------- 截断（字符） ----------

@pytest.mark.asyncio
async def test_content_truncated_by_chars_when_hygiene_enabled(ctx):
    """context_max_chars 生效：超长工具结果文本被截断，保留分页提示，图正常走完。"""
    await seed_bills(ctx['db'])
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver(), context_max_chars=80)
    result = await run_to_end(graph, initial_input('h-ch', '查账单'),
                              {'configurable': {'thread_id': 'h-ch'}})
    tool_msg = result['messages'][-2]
    assert '已截断' in tool_msg.content
    assert len(tool_msg.content) <= 80 + 40  # 截断前缀 + 提示后缀
    assert result['final'] == '查询完成。'


@pytest.mark.asyncio
async def test_hygiene_disabled_by_default_passthrough(ctx):
    """未配置 context_max_chars/context_max_lines 时保持原透传行为（不截断）。"""
    await seed_bills(ctx['db'])
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    result = await run_to_end(graph, initial_input('h-no', '查账单'),
                              {'configurable': {'thread_id': 'h-no'}})
    tool_msg = result['messages'][-2]
    assert '已截断' not in tool_msg.content
    assert '查询范围：' in tool_msg.content


# ---------- 行数提炼 ----------

@pytest.mark.asyncio
async def test_content_lines_truncated(ctx):
    """context_max_lines 生效：超行结果保留前 N 行 + 省略提示（列表型结果防上下文爆炸）。"""
    await seed_cards(ctx['db'], n=5)
    llm = FakeChatModel(messages=iter([
        tool_call_message('list_tracking_cards', {}),
        AIMessage(content='看板已读。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver(), context_max_lines=3)
    result = await run_to_end(graph, initial_input('h-ln', '看卡片'),
                              {'configurable': {'thread_id': 'h-ln'}})
    tool_msg = result['messages'][-2]
    lines = tool_msg.content.split('\n')
    assert '已省略' in tool_msg.content
    # 首行标题 + 前 3 行内容 + 省略提示行
    assert len(lines) <= 5
    assert result['final'] == '看板已读。'


# ---------- 分页 ----------

@pytest.mark.asyncio
async def test_query_bill_offset_pagination(ctx):
    """query_bill 支持 offset 翻页：跳页返回正确明细，summary 提示还有未显示。"""
    await seed_bills(ctx['db'])
    qb = next(t for t in ctx['tools'] if t.name == 'query_bill')
    content, result = await _invoke(qb, {'limit': 1, 'offset': 1, 'direction': None})
    assert result.returned_count == 1 and result.count == 3
    # 按 tx_time desc：c(12:00) → b(11:00) → a(10:00)；offset=1 取到 b
    assert result.items[0].tx_no == 'b'
    assert '还有 1 条未显示' in content
    assert 'offset=2' in content


@pytest.mark.asyncio
async def test_list_tracking_cards_pagination(ctx):
    """list_tracking_cards 分页：limit/offset 生效，summary 提示继续翻页。"""
    await seed_cards(ctx['db'], n=3)
    lt = next(t for t in ctx['tools'] if t.name == 'list_tracking_cards')
    content, cards = await _invoke(lt, {'limit': 1, 'offset': 0})
    assert len(cards.items) == 1 and cards.items[0].id == 3  # id 倒序第一张
    assert '还有 2 张未显示' in content
    assert 'offset=1' in content


async def _invoke(tool_obj, args):
    msg = await tool_obj.arun(args, tool_call_id='call-test')
    return msg.content, msg.artifact


# ---------- 结果缓存 ----------

def build_counting_tool(calls):
    """一个每次执行都计数的只读工具，用于验证缓存命中跳过执行。"""

    @tool('counting_tool', args_schema=EmptyArgs, response_format='content_and_artifact')
    async def counting_tool(**kwargs):
        """只读计数工具：每次执行追加一条记录，返回累计次数（缓存命中时不会执行）。"""
        calls.append(kwargs)
        return f'执行了 {len(calls)} 次', {'n': len(calls)}

    return counting_tool


@pytest.mark.asyncio
async def test_cache_hits_same_args_skips_execution():
    """同参重复调用：第二次命中缓存，工具不重跑，事件带 cached=True。"""
    calls = []
    llm = FakeChatModel(messages=iter([
        tool_call_message('counting_tool', {'a': 1, 'b': 2}, 'call-1'),
        tool_call_message('counting_tool', {'a': 1, 'b': 2}, 'call-2'),
        AIMessage(content='完成。'),
    ]))
    graph = make_graph(llm, [build_counting_tool(calls)], WRITE_TOOLS, MemorySaver(),
                       tool_cache=ToolResultCache(ttl=60), cacheable_tools={'counting_tool'})
    result = await run_to_end(graph, initial_input('c-1', '跑工具'),
                              {'configurable': {'thread_id': 'c-1'}})
    assert len(calls) == 1  # 第二次未执行
    results = [e['payload']['result'] for e in result['events'] if e['type'] == 'tool_result']
    assert len(results) == 2
    assert results[0]['cached'] is False and results[0]['ok'] is True
    assert results[1]['cached'] is True and results[1]['ok'] is True
    assert results[1]['invocation_id'] is None
    assert result['final'] == '完成。'


@pytest.mark.asyncio
async def test_cache_key_normalizes_arg_order():
    """参数顺序不同但内容相同 → 命中同一缓存（键排序规范化）。"""
    calls = []
    llm = FakeChatModel(messages=iter([
        tool_call_message('counting_tool', {'a': 1, 'b': 2}, 'call-1'),
        tool_call_message('counting_tool', {'b': 2, 'a': 1}, 'call-2'),
        AIMessage(content='完成。'),
    ]))
    graph = make_graph(llm, [build_counting_tool(calls)], WRITE_TOOLS, MemorySaver(),
                       tool_cache=ToolResultCache(ttl=60), cacheable_tools={'counting_tool'})
    result = await run_to_end(graph, initial_input('c-2', '跑工具'),
                              {'configurable': {'thread_id': 'c-2'}})
    assert len(calls) == 1
    assert result['final'] == '完成。'


@pytest.mark.asyncio
async def test_cache_expires_after_ttl():
    """TTL 过期后同参再调 → 缓存失效需重新执行（验证 ToolResultCache 过期语义）。"""
    cache = ToolResultCache(ttl=0.05)
    key = ('counting_tool', '{"a": 1}')
    cache.set(key, 'c', {'n': 1})
    assert cache.get(key) is not None
    await asyncio.sleep(0.1)
    assert cache.get(key) is None


@pytest.mark.asyncio
async def test_cache_only_for_declared_tools():
    """不在 cacheable_tools 里的工具不缓存：同参两次都真实执行。"""
    calls = []
    llm = FakeChatModel(messages=iter([
        tool_call_message('counting_tool', {'a': 1}, 'call-1'),
        tool_call_message('counting_tool', {'a': 1}, 'call-2'),
        AIMessage(content='完成。'),
    ]))
    # 不传 cacheable_tools → 不启用缓存
    graph = make_graph(llm, [build_counting_tool(calls)], WRITE_TOOLS, MemorySaver(),
                       tool_cache=ToolResultCache(ttl=60))
    result = await run_to_end(graph, initial_input('c-4', '跑工具'),
                              {'configurable': {'thread_id': 'c-4'}})
    assert len(calls) == 2
    assert result['final'] == '完成。'


@pytest.mark.asyncio
async def test_cache_hit_still_records_last_query(ctx):
    """query_bill 缓存命中仍更新查询记忆（last_query），后续补充条件可用。"""
    await seed_bills(ctx['db'])
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}, 'call-q1'),
        tool_call_message('query_bill', {'category': '餐饮'}, 'call-q2'),
        AIMessage(content='完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver(),
                       tool_cache=ToolResultCache(ttl=60), cacheable_tools=CACHEABLE_TOOLS)
    result = await run_to_end(graph, initial_input('c-5', '查账单'),
                              {'configurable': {'thread_id': 'c-5'}})
    results = [e['payload']['result'] for e in result['events'] if e['type'] == 'tool_result']
    assert results[1]['cached'] is True
    assert result['last_query']['count'] == 1
    assert result['last_query']['total'] == '35.00'
    assert result['final'] == '完成。'
