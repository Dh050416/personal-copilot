"""查询上下文记忆：query_bill 落库范围被记录为 last_query，续轮动态注入模型并约束补充条件。"""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from app.agent.graph import initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import Bill


class RecordingChatModel(GenericFakeChatModel):
    """记录每次模型调用收到的消息内容，用于断言记忆注入。"""

    _calls: list[list[str]] = PrivateAttr(default_factory=list)

    def __init__(self, messages):
        super().__init__(messages=iter(messages))

    @property
    def calls(self):
        return self._calls

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """流式入口：复用 _agenerate 产出一个完整 chunk，保证流式路径与普通调用语义一致。"""
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

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append([str(getattr(m, 'content', '')) for m in messages])
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'memory.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


async def seed_bills(db):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no='a', tx_time='2026-09-01T10:00:00', merchant='曹旋 (83493)', product='群收款',
                     amount_cents=1400, direction='income', transaction_type='群收款',
                     payment_method='零钱', transaction_status='已收钱', merchant_tx_no='', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=1),
                Bill(tx_no='b', tx_time='2026-09-01T19:00:00', merchant='赵记 心乐米线孟州路店', product='米线',
                     amount_cents=9100, direction='expense', transaction_type='商户消费',
                     payment_method='零钱', transaction_status='支付成功', merchant_tx_no='', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=2),
                Bill(tx_no='c', tx_time='2026-09-05T12:00:00', merchant='星巴克', product='咖啡',
                     amount_cents=3200, direction='expense', transaction_type='商户消费',
                     payment_method='银行卡', transaction_status='支付成功', merchant_tx_no='', note='',
                     category='餐饮', category_source='rule', category_confidence=.9,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=3),
            ])


async def run_loop(graph, graph_input, config):
    """循环段式执行：自动放行输入检查点（resume 无输入）直到图跑完 END，语义等同 chat.py 的 runner。"""
    from langgraph.types import Command

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
            return
        current = Command(resume={'new_input': None})


async def run_graph(recorder, tools, graph_input, thread_id):
    graph = make_graph(recorder, tools, WRITE_TOOLS, MemorySaver())
    config = {'configurable': {'thread_id': thread_id}}
    await run_loop(graph, graph_input, config)
    return graph


@pytest.mark.asyncio
async def test_query_records_last_query(ctx):
    recorder = RecordingChatModel([
        tool_call_message('query_bill', {'start_date': '2026-09-01', 'end_date': '2026-09-01'}),
        AIMessage(content='查完了。'),
    ])
    await seed_bills(ctx['db'])
    graph = await run_graph(recorder, ctx['tools'], initial_input('t1', '查9月1日的账单'), 't1')
    snapshot = await graph.aget_state({'configurable': {'thread_id': 't1'}})
    last = snapshot.values.get('last_query')
    assert last is not None
    assert last['filters']['start_date'] == '2026-09-01'
    assert last['filters']['end_date'] == '2026-09-01'
    assert last['count'] == 1
    assert last['total'] == '91.00'


@pytest.mark.asyncio
async def test_second_turn_injects_memory_and_keeps_history_clean(ctx):
    await seed_bills(ctx['db'])
    recorder = RecordingChatModel([
        tool_call_message('query_bill', {'start_date': '2026-09-01', 'end_date': '2026-09-01'}),
        AIMessage(content='查完了。'),
        AIMessage(content='已按9月1日范围筛选商户消费。'),
    ])
    graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    config = {'configurable': {'thread_id': 't2'}}
    await run_loop(graph, initial_input('t2', '查9月1日的账单'), config)
    snapshot = await graph.aget_state(config)
    history = list(snapshot.values['messages'])
    second_input = {'messages': [*history, HumanMessage(content='只要商户消费类型')]}
    await run_loop(graph, second_input, config)
    # 第二轮模型收到记忆文本，且包含 9月1日 范围
    assert len(recorder.calls) == 3
    second_call = recorder.calls[2]
    assert any('当前对话记忆' in c for c in second_call)
    assert any('2026-09-01' in c for c in second_call)
    # 记忆是临时注入的，不得污染 checkpoint 历史（历史里不得出现记忆消息的专属前缀；
    # 注意规则文本含「当前对话记忆」字样，需用记忆前缀精确匹配）
    final_snap = await graph.aget_state({'configurable': {'thread_id': 't2'}})
    hist_texts = [str(getattr(m, 'content', '')) for m in final_snap.values['messages']]
    assert not any('当前对话记忆·最近一次账单查询' in t for t in hist_texts)
