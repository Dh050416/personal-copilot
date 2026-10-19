"""检查点注入（执行中可追加任务，Codex 模式）：
- 图级：agent 每轮之间经过 input_checkpoint 节点 interrupt，外部 resume 可注入
  新 HumanMessage 继续执行（不重跑已完成步骤，checkpoint 保证历史在）。
- chat 层：thread 忙时新消息入队返回 {queued:true}；END 竞态残留并入下一轮输入。
"""
import json
from collections import deque

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import PrivateAttr

from app.agent.graph import initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.main import app


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'inject.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """流式入口：复用 _agenerate 产出一个完整 chunk。"""
        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        chunk_kwargs = {'content': msg.content or ''}
        if getattr(msg, 'tool_calls', None):
            chunk_kwargs['tool_call_chunks'] = [
                {'name': tc['name'], 'args': json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk
        yield ChatGenerationChunk(message=AIMessageChunk(**chunk_kwargs))


class RecordingChatModel(FakeChatModel):
    """记录每次模型调用收到的消息内容，用于断言追加输入进入模型上下文。"""

    _calls: list[list[str]] = PrivateAttr(default_factory=list)

    def __init__(self, messages):
        super().__init__(messages=iter(messages))

    @property
    def calls(self):
        return self._calls

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append([str(getattr(m, 'content', '')) for m in messages])
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


async def run_to_end(graph, graph_input, config):
    """循环放行：input_checkpoint 自动 resume(None) 继续，直到 END；其它 interrupt 停住返回。"""
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


# ---------- 图级：检查点注入 ----------

@pytest.mark.asyncio
async def test_inject_human_input_at_checkpoint_then_continue(ctx):
    """执行中追加输入：在输入检查点注入 HumanMessage → agent 继续（不重跑已完成步骤）。"""
    recorder = RecordingChatModel([
        AIMessage(content='第一步完成。'),
        AIMessage(content='已追加处理。'),
    ])
    graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    config = {'configurable': {'thread_id': 'inj-1'}}
    # 第一段：agent 输出 final 后停在输入检查点
    result = await graph.ainvoke(initial_input('inj-1', '查这个月账单'), config)
    interrupts = result.get('__interrupt__', [])
    assert any(isinstance(i.value, dict) and i.value.get('kind') == 'input_checkpoint'
               for i in interrupts)
    # 用户追加输入 → 注入继续，直到 END
    result = await run_to_end(graph, Command(resume={'new_input': '再加一笔'}), config)
    assert result['final'] == '已追加处理。'
    assert len(recorder.calls) == 2
    assert any('再加一笔' in c for c in recorder.calls[1])
    # 追加的 HumanMessage 已写入 checkpoint 历史（后续轮次可见，不重跑已完成的步骤）
    snap = await graph.aget_state(config)
    texts = [str(getattr(m, 'content', '')) for m in snap.values['messages']]
    assert sum(1 for t in texts if t == '再加一笔') == 1


@pytest.mark.asyncio
async def test_no_input_auto_passes_checkpoint(ctx):
    """无追加输入时输入检查点自动放行：不产生额外模型调用，直接 END。"""
    recorder = RecordingChatModel([AIMessage(content='完成。')])
    graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    result = await run_to_end(graph, initial_input('inj-2', '查账单'),
                              {'configurable': {'thread_id': 'inj-2'}})
    assert result['final'] == '完成。'
    assert len(recorder.calls) == 1


# ---------- chat 层：排队与队列合并 ----------

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


def test_busy_thread_queues_message(ctx):
    """thread 忙（执行流在跑）时新消息返回 {queued:true} 并入队，不并发执行。"""
    llm = FakeChatModel(messages=iter([AIMessage(content='ok')]))
    app.state.graph = make_graph(llm, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    # 模拟已有执行流在跑
    app.state.thread_runtime = {'busy-t': {'active': True, 'pending': deque(),
                                           'waiting_approval': False}}
    resp = TestClient(app).post('/api/chat', json={'message': '追加', 'session_id': 'busy-t'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['queued'] is True
    assert list(app.state.thread_runtime['busy-t']['pending']) == ['追加']


class LoopingFakeChatModel(FakeChatModel):
    """每次都返回 query_bill 工具调用（只读、不触发审批），模拟模型停不下来的循环。"""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        call_id = f'call-loop-{len(messages)}'
        args = {'category': '餐饮'}
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk
        yield ChatGenerationChunk(message=AIMessageChunk(
            content='',
            tool_call_chunks=[{'name': 'query_bill', 'args': json.dumps(args),
                               'id': call_id, 'index': 0}],
        ))


def test_runner_segment_guard_stops_infinite_loop(ctx):
    """runner 段数护栏：LangGraph recursion_limit 不跨 resume 累积（每轮被 check_input
    截断成段），循环 runner 必须自己统计段数，无限循环时强制停止并返回友好 final
    （防止 SSE 永久挂起）。"""
    app.state.graph = make_graph(LoopingFakeChatModel(messages=iter([])), ctx['tools'],
                                 WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    app.state.settings = type('S', (), {'max_steps': 4})()
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '查餐饮', 'session_id': 'loop-t'}) as resp:
        events = parse_events(resp.iter_lines())
    assert events[-1]['type'] == 'final'
    assert '超过了上限' in events[-1]['payload']['text']
    # run 结束后 thread 释放，可再次发起新 run（不残留 busy 状态）
    assert app.state.thread_runtime['loop-t']['active'] is False


def test_leftover_queue_merged_into_next_run(ctx):
    """上一轮 END 竞态残留的排队输入：新一轮 /chat 时合并进初始输入，不丢消息。"""
    recorder = RecordingChatModel([
        AIMessage(content='第一轮。'),
        AIMessage(content='第二轮。'),
    ])
    app.state.graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver())
    app.state.checkpointer = MemorySaver()
    app.state.thread_runtime = {}
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '任务一', 'session_id': 'mix-t'}) as resp:
        first = parse_events(resp.iter_lines())
    assert first[-1]['type'] == 'final' and first[-1]['payload']['text'] == '第一轮。'
    # 模拟 END 竞态：队列残留一条（晚于最后一个输入检查点到达）
    app.state.thread_runtime['mix-t'] = {'active': False, 'pending': deque(['残留追加']),
                                         'waiting_approval': False}
    with TestClient(app).stream('POST', '/api/chat',
                                json={'message': '任务二', 'session_id': 'mix-t'}) as resp:
        second = parse_events(resp.iter_lines())
    assert second[-1]['type'] == 'final' and second[-1]['payload']['text'] == '第二轮。'
    # 第二轮模型同时看到新消息与残留追加
    call = recorder.calls[-1]
    assert sum(1 for c in call if c == '任务二') == 1
    assert sum(1 for c in call if c == '残留追加') == 1
