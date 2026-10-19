"""运行时治理测试：工具超时 / run 整体超时 / 工具调用次数护栏 / 审批 TTL / 模型退避重试。

对应实现：
- graph.py make_graph(tool_timeout / model_retries / retry_backoff) + approve 记录 created_at
- chat.py stream_graph(run_timeout / max_tool_calls) + resume 审批 TTL 校验
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import PrivateAttr

from app.agent.graph import initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.main import app


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'governance.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        import json as _json

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        chunk_kwargs = {'content': msg.content or ''}
        if getattr(msg, 'tool_calls', None):
            chunk_kwargs['tool_call_chunks'] = [
                {'name': tc['name'], 'args': _json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        yield ChatGenerationChunk(message=AIMessageChunk(**chunk_kwargs))


class FlakyChatModel(FakeChatModel):
    """前 fail_times 次模型调用抛错，验证指数退避重试。"""

    _fail_remaining: int = PrivateAttr(default=1)

    def __init__(self, messages, fail_times=1):
        super().__init__(messages=iter(messages))
        self._fail_remaining = fail_times

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise TimeoutError('Request timed out.')
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


class LoopingFakeChatModel(FakeChatModel):
    """每次都返回 query_bill 工具调用（只读、不触发审批），模拟模型停不下来的循环。"""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        call_id = f'call-loop-{len(messages)}'
        args = {'category': '餐饮'}
        yield ChatGenerationChunk(message=AIMessageChunk(
            content='',
            tool_call_chunks=[{'name': 'query_bill', 'args': json.dumps(args),
                               'id': call_id, 'index': 0}],
        ))


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


def fake_settings(**kwargs):
    base = {'max_steps': 50, 'run_timeout_seconds': 60, 'max_tool_calls_per_run': 100,
            'approval_ttl_seconds': 3600}
    base.update(kwargs)
    return type('S', (), base)()


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


# ---------- 工具超时 ----------

@pytest.mark.asyncio
async def test_tool_timeout_returns_structured_error():
    """单次工具执行超过 tool_timeout → TOOL_TIMEOUT 结构化错误回喂模型，图继续不崩溃。"""
    from app.schemas import EmptyArgs

    @tool('slow_tool', args_schema=EmptyArgs, response_format='content_and_artifact')
    async def slow_tool():
        """一个会长时间挂起的工具，用于验证工具执行超时治理。"""
        await asyncio.sleep(30)
        return 'done', {'ok': True}

    llm = FakeChatModel(messages=iter([
        tool_call_message('slow_tool', {}),
        AIMessage(content='工具超时了，我换个说法。'),
    ]))
    graph = make_graph(llm, [slow_tool], WRITE_TOOLS, MemorySaver(), tool_timeout=0.2)
    result = await run_to_end(graph, initial_input('t-t', '跑个慢工具'),
                              {'configurable': {'thread_id': 't-t'}})
    tool_event = next(e for e in result['events'] if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is False
    err = tool_event['payload']['result']['error']
    assert err['code'] == 'TOOL_TIMEOUT'
    assert err['stage'] == 'execution'
    assert err['retryable'] is True
    assert result['final'] == '工具超时了，我换个说法。'


@pytest.mark.asyncio
async def test_no_tool_timeout_keeps_original_behavior(ctx):
    """tool_timeout 未配置（None）时工具正常执行，不引入额外限制。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    result = await run_to_end(graph, initial_input('nt-1', '查账单'),
                              {'configurable': {'thread_id': 'nt-1'}})
    assert result['final'] == '查询完成。'


# ---------- run 整体超时 ----------

def test_run_timeout_stops_with_friendly_final(ctx):
    """run 超过 run_timeout_seconds → 友好 final（时间上限），无 error 事件，thread 释放。"""
    app.state.graph = make_graph(LoopingFakeChatModel(messages=iter([])), ctx['tools'],
                                 WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    app.state.settings = fake_settings(run_timeout_seconds=0.3, max_steps=1000)
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '查餐饮', 'session_id': 'rt-t'}) as resp:
        events = parse_events(resp.iter_lines())
    assert events[-1]['type'] == 'final'
    assert '时间上限' in events[-1]['payload']['text']
    assert 'error' not in [e['type'] for e in events]
    assert app.state.thread_runtime['rt-t']['active'] is False


# ---------- 工具调用次数护栏 ----------

def test_max_tool_calls_stops_run(ctx):
    """工具调用达到 max_tool_calls_per_run → 友好 final，不再继续执行。"""
    app.state.graph = make_graph(LoopingFakeChatModel(messages=iter([])), ctx['tools'],
                                 WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    app.state.settings = fake_settings(max_tool_calls_per_run=3, max_steps=1000)
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '查餐饮', 'session_id': 'mt-t'}) as resp:
        events = parse_events(resp.iter_lines())
    finals = [e for e in events if e['type'] == 'final']
    assert finals and '工具调用次数' in finals[-1]['payload']['text']
    tool_events = [e for e in events if e['type'] == 'tool_result']
    assert len(tool_events) == 3
    assert app.state.thread_runtime['mt-t']['active'] is False


# ---------- 审批 TTL ----------

def test_approval_expired_rejected(ctx, monkeypatch):
    """审批创建超过 approval_ttl_seconds → resume 返回 410，拒绝恢复。"""
    import app.agent.graph as graph_mod

    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}),
        AIMessage(content='卡片已创建。'),
    ]))
    app.state.graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    # 把 approve 节点的 now_iso 替换成过去时间，制造"创建于很久之前"的审批
    monkeypatch.setattr(graph_mod, 'now_iso', lambda: '2020-01-01T00:00:00+00:00')
    app.state.settings = fake_settings(approval_ttl_seconds=3600)
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '建卡', 'session_id': 'ttl-t'}) as resp:
        first = parse_events(resp.iter_lines())
    approval = next(e for e in first if e['type'] == 'approval_created')
    with TestClient(app).stream(
            'POST', f"/api/runs/{approval['run_id']}/resume",
            json={'approval_id': approval['payload']['approval_id'], 'decision': 'approve'}) as resp:
        assert resp.status_code == 410


def test_approval_within_ttl_resumes_ok(ctx):
    """审批未过期 → resume 正常执行（正路径保护）。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}),
        AIMessage(content='卡片已创建。'),
    ]))
    app.state.graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    app.state.settings = fake_settings(approval_ttl_seconds=3600)
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '建卡', 'session_id': 'ttl-ok'}) as resp:
        first = parse_events(resp.iter_lines())
    approval = next(e for e in first if e['type'] == 'approval_created')
    with TestClient(app).stream(
            'POST', f"/api/runs/{approval['run_id']}/resume",
            json={'approval_id': approval['payload']['approval_id'], 'decision': 'approve'}) as resp:
        second = parse_events(resp.iter_lines())
    assert second[-1]['type'] == 'final' and second[-1]['payload']['text'] == '卡片已创建。'


# ---------- 输出契约校验 ----------

@pytest.mark.asyncio
async def test_output_schema_valid_passes(ctx):
    """输出契约校验：合法输出通过统一出口校验，artifact 为校验后的 JSON。"""
    from app.agent.tools import OUTPUT_SCHEMAS

    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver(),
                       output_schemas=OUTPUT_SCHEMAS)
    result = await run_to_end(graph, initial_input('out-ok', '查账单'),
                              {'configurable': {'thread_id': 'out-ok'}})
    tool_event = next(e for e in result['events'] if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is True
    data = tool_event['payload']['result']['data']
    assert data['count'] >= 0 and 'filters' in data and 'items' in data
    assert result['final'] == '查询完成。'


@pytest.mark.asyncio
async def test_output_schema_invalid_becomes_output_invalid():
    """工具返回结构不符合输出契约 → OUTPUT_INVALID 结构化错误，结果不进状态，模型可继续。"""
    from pydantic import BaseModel

    from app.schemas import EmptyArgs

    class BadOut(BaseModel):
        required_field: str

    @tool('weird_tool', args_schema=EmptyArgs, response_format='content_and_artifact')
    async def weird_tool():
        """返回一个不符合输出契约的结构（缺 required_field）。"""
        return '完成', {'wrong_key': 'x'}

    llm = FakeChatModel(messages=iter([
        tool_call_message('weird_tool', {}),
        AIMessage(content='输出有问题，我换个说法。'),
    ]))
    graph = make_graph(llm, [weird_tool], WRITE_TOOLS, MemorySaver(),
                       output_schemas={'weird_tool': BadOut})
    result = await run_to_end(graph, initial_input('out-bad', '跑工具'),
                              {'configurable': {'thread_id': 'out-bad'}})
    tool_event = next(e for e in result['events'] if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is False
    err = tool_event['payload']['result']['error']
    assert err['code'] == 'OUTPUT_INVALID'
    assert err['stage'] == 'output'
    assert err['retryable'] is False
    assert result['final'] == '输出有问题，我换个说法。'


@pytest.mark.asyncio
async def test_output_schema_none_keeps_passthrough(ctx):
    """未配置 output_schemas（默认）时不校验，保持原透传行为。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成。'),
    ]))
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    result = await run_to_end(graph, initial_input('out-none', '查账单'),
                              {'configurable': {'thread_id': 'out-none'}})
    tool_event = next(e for e in result['events'] if e['type'] == 'tool_result')
    assert tool_event['payload']['result']['ok'] is True
    assert result['final'] == '查询完成。'


# ---------- 模型指数退避重试 ----------

@pytest.mark.asyncio
async def test_model_retries_with_backoff_recovers():
    """模型前 2 次调用失败 → 指数退避重试 2 次后成功，run 正常完成。"""
    llm = FlakyChatModel([AIMessage(content='重试成功。')], fail_times=2)
    graph = make_graph(llm, [], WRITE_TOOLS, MemorySaver(), model_retries=2, retry_backoff=0.01)
    result = await run_to_end(graph, initial_input('mr-1', '查账单'),
                              {'configurable': {'thread_id': 'mr-1'}})
    assert result['final'] == '重试成功。'


@pytest.mark.asyncio
async def test_model_exhausts_retries_returns_final():
    """模型持续失败且重试耗尽 → 可读 final（模型响应暂时不可用），不把 run 打成 RUN_FAILED。"""
    llm = FlakyChatModel([], fail_times=99)
    graph = make_graph(llm, [], WRITE_TOOLS, MemorySaver(), model_retries=1, retry_backoff=0.01)
    result = await run_to_end(graph, initial_input('mr-2', '查账单'),
                              {'configurable': {'thread_id': 'mr-2'}})
    assert '模型响应暂时不可用' in result['final']
