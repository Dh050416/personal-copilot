"""递进摘要（滑动窗口）测试：验证 _trim_history_with_summary 的核心行为。

覆盖：
1. 消息未超窗口 → 不摘要、不写库、原样保留（零开销路径）。
2. 消息超窗口 → 被挤出的普通文本消息增量合并进摘要，注入 system。
3. 工具调用 / 系统消息不参与摘要（is_summarizable 过滤）。
4. 首条 Human（任务目标）永不截断。
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.api.chat import _trim_history_with_summary
from app.services.summary import is_summarizable


class _FakeSummaryManager:
    def __init__(self):
        self.calls = []
        self._store = {}

    async def update(self, thread_id, user_id, new_messages):
        self.calls.append((thread_id, user_id, list(new_messages)))
        existing = self._store.get(thread_id, '')
        merged = ' | '.join(new_messages)
        self._store[thread_id] = f'{existing} {merged}'.strip()
        return self._store[thread_id]


def _mk_history(n):
    """构造 n 条普通文本消息：System + 交替 Human/AI 文本。"""
    msgs = [SystemMessage(content='铁律')]
    for i in range(n):
        if i % 2 == 0:
            msgs.append(HumanMessage(content=f'问{i}'))
        else:
            msgs.append(AIMessage(content=f'答{i}'))
    return msgs


@pytest.mark.asyncio
async def test_under_window_no_summary():
    mgr = _FakeSummaryManager()
    history = _mk_history(5)  # System + 5 条正文
    out = await _trim_history_with_summary(history, 't1', 1, mgr, window_size=10)
    # 未超窗口：不调用 summary，消息原样保留
    assert mgr.calls == []
    assert len(out) == len(history)


@pytest.mark.asyncio
async def test_over_window_triggers_summary():
    mgr = _FakeSummaryManager()
    # System + 12 条正文（Human/AI 交替），窗口=5 → 挤出 7 条
    history = _mk_history(12)
    out = await _trim_history_with_summary(history, 't1', 1, mgr, window_size=5)
    # 触发了摘要
    assert len(mgr.calls) == 1
    thread_id, user_id, summarized = mgr.calls[0]
    assert thread_id == 't1' and user_id == 1
    # 摘要内容应包含被挤出的文本
    assert any('问0' in s or '答1' in s for s in summarized)
    # 摘要注入到了 system
    assert '【对话摘要】' in out[0].content


@pytest.mark.asyncio
async def test_tool_messages_not_summarized():
    mgr = _FakeSummaryManager()
    # 构造含工具调用的消息：这些不该进摘要
    tool_ai = AIMessage(content='', tool_calls=[{'name': 'query_bill', 'args': {}, 'id': 'c1'}])
    tool_msg = ToolMessage(content='结果', tool_call_id='c1')
    msgs = [
        SystemMessage(content='铁律'),
        HumanMessage(content='查账单'),
        tool_ai,
        tool_msg,
        AIMessage(content='查到3条'),
        HumanMessage(content='建卡'),
        AIMessage(content='好的'),
        HumanMessage(content='继续'),
        AIMessage(content='处理中'),
    ]
    out = await _trim_history_with_summary(msgs, 't1', 1, mgr, window_size=3)
    # 摘要只吸收普通文本，不含工具调用内容
    if mgr.calls:
        summarized_text = ' '.join(s for call in mgr.calls for s in call[2])
        assert 'tool_calls' not in summarized_text


def test_is_summarizable_filters():
    assert is_summarizable(HumanMessage(content='你好')) is True
    assert is_summarizable(AIMessage(content='回复')) is True
    # 带 tool_calls 的 AI 消息不摘要
    assert is_summarizable(AIMessage(content='', tool_calls=[{'name': 'x', 'args': {}, 'id': '1'}])) is False
    # System / Tool 消息不摘要
    assert is_summarizable(SystemMessage(content='规则')) is False
    assert is_summarizable(ToolMessage(content='结果', tool_call_id='1')) is False
    # 空文本不摘要
    assert is_summarizable(HumanMessage(content='')) is False


@pytest.mark.asyncio
async def test_window_disabled_falls_back():
    mgr = _FakeSummaryManager()
    history = _mk_history(15)
    out = await _trim_history_with_summary(history, 't1', 1, mgr, window_size=0)
    # window_size=0 时退化为旧全量截断，不调摘要
    assert mgr.calls == []
