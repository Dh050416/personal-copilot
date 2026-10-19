"""SSE 事件协议测试：保持前端零改动契约（{run_id,type,payload} + [DONE]），含 HITL resume。"""
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from app.agent.graph import make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import Bill
from app.main import app


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'api.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """流式入口：复用 _agenerate 产出一个完整 chunk。
        graph 用 astream 后强制走模型流式路径，子类只需覆写 _agenerate
        即可在流式路径下保留记录/抛错语义。"""
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


class RecordingChatModel(FakeChatModel):
    """记录每次模型调用收到的消息内容，用于断言多轮上下文衔接。"""

    _calls: list[list[str]] = PrivateAttr(default_factory=list)

    def __init__(self, messages):
        super().__init__(messages=iter(messages))

    @property
    def calls(self) -> list[list[str]]:
        return self._calls

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append([str(getattr(m, 'content', '')) for m in messages])
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


class FlakyChatModel(FakeChatModel):
    """前 fail_times 次模型调用抛超时，验证 agent 节点自愈重试。"""

    _fail_remaining: int = PrivateAttr(default=1)

    def __init__(self, messages, fail_times=1):
        super().__init__(messages=iter(messages))
        self._fail_remaining = fail_times

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise TimeoutError('Request timed out.')
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def install_graph(tools, *messages):
    llm = FakeChatModel(messages=iter(messages))
    app.state.graph = make_graph(llm, tools, WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


def parse_events(lines):
    events = []
    for line in lines:
        if not line or not line.startswith('data: '):
            continue
        data = line[len('data: '):]
        if data == '[DONE]':
            break
        events.append(json.loads(data))
    return events


async def seed_bills(db):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no='a', tx_time='2026-09-01T10:00:00', merchant='张三', product='转账',
                     amount_cents=10000, direction='expense', transaction_type='转账',
                     payment_method='零钱', transaction_status='已转账', merchant_tx_no='ma', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=1),
                Bill(tx_no='c', tx_time='2026-09-01T12:00:00', merchant='咖啡店', product='咖啡',
                     amount_cents=3500, direction='expense', transaction_type='商户消费',
                     payment_method='零钱', transaction_status='支付成功', merchant_tx_no='mc', note='',
                     category='餐饮', category_source='rule', category_confidence=.95,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=3),
            ])


def test_readonly_chat_emits_protocol_events(ctx):
    install_graph(ctx['tools'],
                  tool_call_message('query_bill', {'category': '餐饮'}),
                  AIMessage(content='查询完成。'))
    with TestClient(app).stream('POST', '/api/chat', json={'message': '查餐饮'}) as resp:
        assert resp.status_code == 200
        events = parse_events(resp.iter_lines())
    types = [e['type'] for e in events]
    # stream 为新增的流式 token 事件（模型回答实时推送），次数不定，过滤后校验核心协议
    core_types = [t for t in types if t != 'stream']
    assert core_types == ['intent_route', 'tool_call_proposal', 'arguments_validated',
                          'tool_result', 'final']
    assert all(e['run_id'] for e in events)
    tool_event = next(e for e in events if e['type'] == 'tool_result')
    assert tool_event['payload']['tool'] == 'query_bill'
    assert tool_event['payload']['result']['ok'] is True
    assert tool_event['payload']['result']['data']['count'] == 0
    assert events[-1]['type'] == 'final'
    assert events[-1]['payload']['text'] == '查询完成。'


def test_write_chat_emits_approval_then_resume_approve(ctx):
    install_graph(ctx['tools'],
                  tool_call_message('create_tracking_card',
                                    {'category': '餐饮', 'purpose': '报销'}),
                  AIMessage(content='卡片已创建。'))
    with TestClient(app).stream('POST', '/api/chat', json={'message': '建卡'}) as resp:
        first = parse_events(resp.iter_lines())
    types = [e['type'] for e in first]
    assert 'approval_created' in types and 'final' not in types
    approval = next(e for e in first if e['type'] == 'approval_created')
    assert approval['payload']['tool'] == 'create_tracking_card'
    assert approval['payload']['args'] == {'category': '餐饮', 'purpose': '报销'}
    run_id, approval_id = approval['run_id'], approval['payload']['approval_id']
    with TestClient(app).stream(
            'POST', f'/api/runs/{run_id}/resume',
            json={'approval_id': approval_id, 'decision': 'approve'}) as resp:
        second = parse_events(resp.iter_lines())
    assert second[-1]['type'] == 'final'
    tool_event = next(e for e in second if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is True
    assert tool_event['payload']['result']['data']['purpose'] == '报销'


def test_write_chat_reject_resumes_with_user_rejected(ctx):
    install_graph(ctx['tools'],
                  tool_call_message('create_tracking_card', {'category': '订阅', 'purpose': '待退订'}),
                  AIMessage(content='好的，已取消。'))
    with TestClient(app).stream('POST', '/api/chat', json={'message': '建卡'}) as resp:
        first = parse_events(resp.iter_lines())
    approval = next(e for e in first if e['type'] == 'approval_created')
    run_id, approval_id = approval['run_id'], approval['payload']['approval_id']
    with TestClient(app).stream(
            'POST', f'/api/runs/{run_id}/resume',
            json={'approval_id': approval_id, 'decision': 'reject'}) as resp:
        second = parse_events(resp.iter_lines())
    tool_event = next(e for e in second if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is False
    assert tool_event['payload']['result']['error']['code'] == 'USER_REJECTED'
    assert second[-1]['type'] == 'final'


def test_resume_unknown_run_and_wrong_approval(ctx):
    install_graph(ctx['tools'], AIMessage(content='ok'))
    with TestClient(app).stream(
            'POST', '/api/runs/no-such-run/resume',
            json={'approval_id': 'x', 'decision': 'approve'}) as resp:
        assert resp.status_code == 404
    install_graph(ctx['tools'], AIMessage(content='ok'))
    with TestClient(app).stream(
            'POST', '/api/runs/another/resume',
            json={'approval_id': 'x', 'decision': 'approve'}) as resp:
        assert resp.status_code in (404, 409)


def test_same_session_second_turn_carries_context(ctx):
    """同一 session_id 的两轮消息共享 thread 历史：第二轮模型必须能看到第一轮内容。"""
    recorder = RecordingChatModel([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='第一轮完成。'),
        AIMessage(content='第二轮基于上下文回答。'),
    ])
    app.state.graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '查9月1日账单', 'session_id': 'sess-x'}) as resp:
        first = parse_events(resp.iter_lines())
    assert first[-1]['type'] == 'final' and first[-1]['payload']['text'] == '第一轮完成。'
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '只要餐饮的', 'session_id': 'sess-x'}) as resp:
        second = parse_events(resp.iter_lines())
    assert second[-1]['type'] == 'final' and second[-1]['payload']['text'] == '第二轮基于上下文回答。'
    # 第二轮不重复注入 System，且模型收到的历史同时包含两轮用户消息
    assert len(recorder.calls) == 3
    second_call = recorder.calls[2]
    assert sum(1 for c in second_call if c == '查9月1日账单') == 1
    assert sum(1 for c in second_call if c == '只要餐饮的') == 1
    assert sum(1 for c in second_call if '个人账单管家' in c) == 1  # System 仅首轮注入一次


def test_new_session_id_starts_fresh_history(ctx):
    """不同 session_id = 独立会话：第二轮模型看不到第一轮内容。"""
    recorder = RecordingChatModel([
        AIMessage(content='第一轮回答。'),
        AIMessage(content='第二轮回答。'),
    ])
    app.state.graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '查9月1日账单', 'session_id': 'sess-a'}) as resp:
        parse_events(resp.iter_lines())
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '只要餐饮的', 'session_id': 'sess-b'}) as resp:
        parse_events(resp.iter_lines())
    assert len(recorder.calls) == 2
    assert not any('查9月1日账单' in c for c in recorder.calls[1])


def test_model_timeout_self_heals_and_completes(ctx):
    """模型调用第一次超时 → 节点提示自愈重试成功 → run 正常完成（无 RUN_FAILED）。"""
    llm = FlakyChatModel([AIMessage(content='重试成功。')], fail_times=1)
    app.state.graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    with TestClient(app).stream('POST', '/api/chat', json={'message': '查账单'}) as resp:
        events = parse_events(resp.iter_lines())
    types = [e['type'] for e in events]
    assert 'error' not in types
    assert events[-1]['type'] == 'final'
    assert events[-1]['payload']['text'] == '重试成功。'


def test_model_timeout_after_retry_returns_readable_final(ctx):
    """模型连续超时 → 返回可读 final，而不是把 run 打成 RUN_FAILED。"""
    llm = FlakyChatModel([], fail_times=99)
    app.state.graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    with TestClient(app).stream('POST', '/api/chat', json={'message': '查账单'}) as resp:
        events = parse_events(resp.iter_lines())
    types = [e['type'] for e in events]
    assert 'error' not in types
    assert events[-1]['type'] == 'final'
    assert '模型响应暂时不可用' in events[-1]['payload']['text']


def test_complete_card_chat_emits_approval_then_resume_approve(ctx):
    """「把卡片移到已完成」走 HITL：第一轮建卡批准，第二轮 complete_tracking_card 批准后 status=done。"""
    install_graph(ctx['tools'],
                  tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}),
                  AIMessage(content='卡片已创建。'),
                  tool_call_message('complete_tracking_card', {'card_id': 1}, call_id='call-2'),
                  AIMessage(content='卡片已完成。'))
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '建卡', 'session_id': 'sess-c'}) as resp:
        first = parse_events(resp.iter_lines())
    approval = next(e for e in first if e['type'] == 'approval_created')
    assert approval['payload']['tool'] == 'create_tracking_card'
    with TestClient(app).stream(
            'POST', f"/api/runs/{approval['run_id']}/resume",
            json={'approval_id': approval['payload']['approval_id'], 'decision': 'approve'}) as resp:
        parse_events(resp.iter_lines())
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '把这张卡移到已完成', 'session_id': 'sess-c'}) as resp:
        second = parse_events(resp.iter_lines())
    approval2 = next(e for e in second if e['type'] == 'approval_created')
    assert approval2['payload']['tool'] == 'complete_tracking_card'
    assert approval2['payload']['args'] == {'card_id': 1}
    with TestClient(app).stream(
            'POST', f"/api/runs/{approval2['run_id']}/resume",
            json={'approval_id': approval2['payload']['approval_id'], 'decision': 'approve'}) as resp:
        third = parse_events(resp.iter_lines())
    assert third[-1]['type'] == 'final' and third[-1]['payload']['text'] == '卡片已完成。'
    tool_event = next(e for e in third if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['data']['status'] == 'done'
