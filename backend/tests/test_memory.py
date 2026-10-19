"""长期记忆（agent_memory）与上下文保护：save_memory 落库/upsert、system 注入、
last_query 过期、_trim_history 首条 Human 保护、save_memory 不触发 HITL。"""
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr
from sqlalchemy import select

from app.agent.graph import build_system_message, initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.api.chat import _trim_history
from app.data.db import Database
from app.data.models import AgentMemory


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


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'memory.db')
    await db.initialize()
    yield {'db': db, 'tools': build_tools(db)}
    await db.close()


async def load_memories(db):
    async with db.sessions() as session:
        return list(await session.scalars(select(AgentMemory).order_by(AgentMemory.updated_at.desc())))


# ---------- save_memory 工具 ----------

@pytest.mark.asyncio
async def test_save_memory_upsert(ctx):
    spec = next(t for t in ctx['tools'] if t.name == 'save_memory')
    await spec.arun({'key': '报销默认分类', 'value': '其他'}, tool_call_id='c1')
    await spec.arun({'key': '章鱼大王', 'value': '常去餐饮店'}, tool_call_id='c2')
    # 覆盖更新同一 key
    await spec.arun({'key': '报销默认分类', 'value': '餐饮'}, tool_call_id='c3')

    rows = await load_memories(ctx['db'])
    by_key = {r.key: r.value for r in rows}
    assert by_key == {'报销默认分类': '餐饮', '章鱼大王': '常去餐饮店'}


@pytest.mark.asyncio
async def test_save_memory_rejects_oversize(ctx):
    spec = next(t for t in ctx['tools'] if t.name == 'save_memory')
    with pytest.raises(Exception) as exc:
        await spec.arun({'key': 'x' * 200, 'value': 'v'}, tool_call_id='c4')
    assert 'validation' in str(exc.value).lower() or 'Invalid' in str(exc.value)


def test_save_memory_not_in_write_tools():
    """保存偏好是低风险记录（用户当面表达的授权），不触发 HITL 审批。"""
    assert 'save_memory' not in WRITE_TOOLS


# ---------- system 注入 ----------

def test_build_system_message_injects_long_term_memory():
    text = build_system_message(['skill_query'], memory_text='报销默认分类=其他；章鱼大王=常去餐饮店')
    assert '【长期记忆】' in text
    assert '报销默认分类=其他' in text
    assert '【流程·查询账单】' in text
    assert '铁律' in text


def test_build_system_message_no_memory_when_none():
    text = build_system_message([])
    assert '【长期记忆】' not in text


@pytest.mark.asyncio
async def test_intent_injects_memory_into_system(ctx):
    recorder = RecordingChatModel([AIMessage(content='好的。')])

    async def loader():
        return '报销默认分类=其他；章鱼大王=常去餐饮店'

    graph = make_graph(recorder, ctx['tools'], WRITE_TOOLS, MemorySaver(), memory_loader=loader)
    async for _ in graph.astream(initial_input('t1', '老样子，查章鱼大王的账单'),
                                 {'configurable': {'thread_id': 't1'}}, stream_mode='updates'):
        pass
    assert recorder.calls
    first_call = recorder.calls[0]
    assert any('【长期记忆】' in c for c in first_call)
    assert any('章鱼大王=常去餐饮店' in c for c in first_call)


# ---------- last_query 过期 ----------

def test_memo_expired_with_stale_timestamp():
    from datetime import datetime, timedelta, timezone

    from app.agent.graph import _query_memo_expired

    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert _query_memo_expired(stale)
    fresh = datetime.now(timezone.utc).isoformat()
    assert not _query_memo_expired(fresh)
    assert not _query_memo_expired(None)  # 旧状态/测试兼容：无时间戳视为有效


def test_query_memo_returns_none_when_expired():
    from app.agent.graph import _query_memo_text

    last_query = {'filters': {'start_date': '2026-09-01'}, 'count': 3, 'total': '120.00'}
    assert _query_memo_text(last_query, expired=True) is None
    assert _query_memo_text(None, expired=False) is None
    assert _query_memo_text(last_query, expired=False) is not None


# ---------- 首条 Human 保护（任务目标永不截断） ----------

def test_trim_history_keeps_first_human_target():
    system = SystemMessage(content='铁律')
    goal = HumanMessage(content='查询八月份账单，并且将转账记录做成卡片放在其他区域')
    msgs = [system, goal]
    for i in range(35):
        msgs.append(AIMessage(content=f'第 {i} 轮回答'))
    trimmed = _trim_history(msgs)
    assert trimmed[0] is system
    # 首条 Human（初始任务目标）必须保留
    assert any(m is goal for m in trimmed)
    # 总条数不超过上限
    assert len(trimmed) <= 30


def test_trim_history_when_first_human_already_in_recent_window():
    system = SystemMessage(content='铁律')
    goal = HumanMessage(content='最近的目标')
    msgs = [system, *[AIMessage(content=f'旧 {i}') for i in range(20)], goal]
    trimmed = _trim_history(msgs)
    assert goal in trimmed
    assert len(trimmed) <= 30
