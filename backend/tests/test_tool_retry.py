"""工具层自动重试测试：只重试瞬时错误（超时 / 显式 retryable），指数退避；
同一 call id 下重跑 → 幂等键贯穿整个重试窗口 → 写工具查重兜底不重复建卡。

覆盖：
- 超时后重试成功：attempts 如实上报，只建一张卡
- 「执行成功但响应超时」重试：幂等键查重命中，重放首次结果，不重复建卡（核心闭环）
- 持续超时重试耗尽：TOOL_TIMEOUT 错误、attempts = tool_retries + 1
- 确定性错误不重试：业务失败 / 参数校验失败 attempts == 1
- 默认 tool_retries=0 保持旧行为：超时一次直接回喂模型
"""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import func, select

from app.agent.graph import initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import TrackingCard
from app.errors import ToolError


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'retry.db')
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


async def card_count(db):
    async with db.sessions() as session:
        return await session.scalar(select(func.count(TrackingCard.id)))


def get_tool(tools, name):
    return next(t for t in tools if t.name == name)


def run_graph(llm, ctx, tool_retries=2, tools=None):
    return make_graph(llm, tools or ctx['tools'], WRITE_TOOLS, MemorySaver(),
                      tool_retries=tool_retries, retry_backoff=0.01)


# ---------- 超时后重试成功 ----------

@pytest.mark.asyncio
async def test_timeout_then_success_retries_and_reports_attempts(ctx, monkeypatch):
    """第一次超时、第二次成功：自动重试，attempts=2，只建一张卡，无错误事件。"""
    tool = get_tool(ctx['tools'], 'create_tracking_card')
    # StructuredTool 是 pydantic 模型（实例 __setattr__ 拒绝非字段属性），
    # 用类级 monkeypatch 注入重试行为（作用域内自动撤销，只影响被调用的工具）。
    cls = type(tool)
    orig = cls.arun
    calls = []

    async def flaky(self, tool_input, tool_call_id=None, **kwargs):
        calls.append(tool_call_id)
        if len(calls) == 1:
            raise TimeoutError('simulated timeout')
        return await orig(self, tool_input, tool_call_id=tool_call_id, **kwargs)

    monkeypatch.setattr(cls, 'arun', flaky)
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r1'),
        AIMessage(content='卡片已创建。'),
    ]))
    result = await run_with_approvals(run_graph(llm, ctx), initial_input('retry-t', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-t'}})
    assert len(calls) == 2                      # 第一次超时 + 第二次成功
    assert await card_count(ctx['db']) == 1     # 只建一张卡
    success = [e['payload']['result'] for e in result['events']
               if e['type'] == 'tool_result' and e['payload']['result']['ok']]
    assert len(success) == 1
    assert success[0]['attempts'] == 2


@pytest.mark.asyncio
async def test_success_but_timeout_retry_is_idempotent(ctx, monkeypatch):
    """核心闭环：第一次执行成功（卡已建）但框架侧判定超时（响应丢失），
    第二次重试复用同一 call id → 幂等键查重命中 → 重放首次结果，不重复建卡。"""
    tool = get_tool(ctx['tools'], 'create_tracking_card')
    cls = type(tool)
    orig = cls.arun
    calls = []

    async def flaky(self, tool_input, tool_call_id=None, **kwargs):
        calls.append(tool_call_id)
        if len(calls) == 1:
            await orig(self, tool_input, tool_call_id=tool_call_id, **kwargs)        # 真实建卡成功
            raise TimeoutError('response lost after success')                        # 但响应丢失
        return await orig(self, tool_input, tool_call_id=tool_call_id, **kwargs)     # 重试：查重命中重放

    monkeypatch.setattr(cls, 'arun', flaky)
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r2'),
        AIMessage(content='卡片已创建。'),
    ]))
    result = await run_with_approvals(run_graph(llm, ctx), initial_input('retry-i', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-i'}})
    assert len(calls) == 2
    assert await card_count(ctx['db']) == 1          # 幂等查重：绝不重复建卡
    success = [e['payload']['result'] for e in result['events']
               if e['type'] == 'tool_result' and e['payload']['result']['ok']]
    assert len(success) == 1
    assert success[0]['attempts'] == 2
    # 键落库：卡表的 invocation_id 就是本次幂等键
    async with ctx['db'].sessions() as session:
        card = await session.scalar(select(TrackingCard).where(TrackingCard.invocation_id == 'apr-call-r2'))
        assert card is not None


# ---------- 重试耗尽 / 确定性错误不重试 ----------

@pytest.mark.asyncio
async def test_persistent_timeout_exhausts_retries(ctx, monkeypatch):
    """持续超时：重试 tool_retries 次后仍失败 → TOOL_TIMEOUT 错误、attempts = retries + 1。"""
    tool = get_tool(ctx['tools'], 'create_tracking_card')
    cls = type(tool)
    monkeypatch.setattr(cls, 'arun',
                        lambda self, *a, **kw: _raise(TimeoutError('always timeout')))
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r3'),
        AIMessage(content='失败。'),
    ]))
    result = await run_with_approvals(run_graph(llm, ctx, tool_retries=2),
                                      initial_input('retry-e', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-e'}})
    errors = [e['payload']['result'] for e in result['events']
              if e['type'] == 'tool_result' and not e['payload']['result']['ok']]
    assert len(errors) == 1
    assert errors[0]['error']['code'] == 'TOOL_TIMEOUT'
    assert errors[0]['error']['retryable'] is True
    assert errors[0]['attempts'] == 3          # 1 次原始 + 2 次重试
    assert await card_count(ctx['db']) == 0


async def _raise(exc):
    raise exc


@pytest.mark.asyncio
async def test_deterministic_business_error_no_retry(ctx, monkeypatch):
    """确定性业务失败（retryable=False）：不重试，attempts=1，直接回喂模型。"""
    tool = get_tool(ctx['tools'], 'create_tracking_card')
    cls = type(tool)
    monkeypatch.setattr(cls, 'arun',
                        lambda self, *a, **kw: _raise(ToolError('TOOL_FAILED', '业务失败', retryable=False)))
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r4'),
        AIMessage(content='失败。'),
    ]))
    result = await run_with_approvals(run_graph(llm, ctx, tool_retries=3),
                                      initial_input('retry-d', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-d'}})
    errors = [e['payload']['result'] for e in result['events']
              if e['type'] == 'tool_result' and not e['payload']['result']['ok']]
    assert len(errors) == 1
    assert errors[0]['error']['code'] == 'TOOL_FAILED'
    assert errors[0]['attempts'] == 1          # 即使 tool_retries=3 也不重试


@pytest.mark.asyncio
async def test_validation_error_no_retry(ctx):
    """参数校验失败（extra 字段被 StrictModel 拒绝）：不重试，attempts=1，INVALID_ARGUMENTS。"""
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销',
                                                   'unknown_field': 1}, 'call-r5'),
        AIMessage(content='失败。'),
    ]))
    result = await run_with_approvals(run_graph(llm, ctx, tool_retries=3),
                                      initial_input('retry-v', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-v'}})
    errors = [e['payload']['result'] for e in result['events']
              if e['type'] == 'tool_result' and not e['payload']['result']['ok']]
    assert len(errors) == 1
    assert errors[0]['error']['code'] == 'INVALID_ARGUMENTS'
    assert errors[0]['attempts'] == 1
    assert await card_count(ctx['db']) == 0


@pytest.mark.asyncio
async def test_default_tool_retries_zero_keeps_old_behavior(ctx, monkeypatch):
    """默认 tool_retries=0（未启用）：超时一次直接回喂模型，attempts=1，行为与改造前一致。"""
    tool = get_tool(ctx['tools'], 'create_tracking_card')
    cls = type(tool)
    monkeypatch.setattr(cls, 'arun',
                        lambda self, *a, **kw: _raise(TimeoutError('simulated timeout')))
    llm = FakeChatModel(messages=iter([
        tool_call_message('create_tracking_card', {'category': '餐饮', 'purpose': '报销'}, 'call-r6'),
        AIMessage(content='失败。'),
    ]))
    # 不传 tool_retries → 默认 0
    graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver(), retry_backoff=0.01)
    result = await run_with_approvals(graph, initial_input('retry-z', '建一张餐饮卡'),
                                      {'configurable': {'thread_id': 'retry-z'}})
    errors = [e['payload']['result'] for e in result['events']
              if e['type'] == 'tool_result' and not e['payload']['result']['ok']]
    assert len(errors) == 1
    assert errors[0]['error']['code'] == 'TOOL_TIMEOUT'
    assert errors[0]['attempts'] == 1
    assert await card_count(ctx['db']) == 0
