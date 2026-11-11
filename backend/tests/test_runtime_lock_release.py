"""thread_runtime 锁泄漏回归。

active 的复位只在 stream_graph 的 runner() finally 里，而它位于 StreamingResponse
生成器内部：端点置位之后、SSE 流被迭代之前只要抛异常，锁就永久泄漏，该会话
此后所有消息只进 pending 队列（永远返回 queued）。本文件锁死修复行为。

不连数据库、不走鉴权：直接调用端点协程，用桩对象模拟 graph。
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.api.chat import chat, get_runtime, resume
from app.schemas import ChatRequest, ResumeRequest


class SettingsStub:
    approval_ttl_seconds = 600
    run_timeout_seconds = 120
    max_steps = 8
    max_tool_calls_per_run = 20
    history_window_size = 10


class BrokenGraph:
    """aget_state 抛异常：模拟 PG 抖动 / checkpoint 读取失败。"""

    def __init__(self, exc=None):
        self.exc = exc or RuntimeError('PG checkpoint 读取失败')

    async def aget_state(self, config):
        raise self.exc


class EmptyGraph:
    """无历史、无挂起任务的正常快照。"""

    async def aget_state(self, config):
        return SimpleNamespace(values={}, tasks=[])


class ApprovalGraph:
    """带一条挂起审批的快照。"""

    def __init__(self, approval_id='ap-1', created_at=None):
        self.approval_id = approval_id
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    async def aget_state(self, config):
        interrupt = SimpleNamespace(value={
            'approval_id': self.approval_id,
            'created_at': self.created_at,
        })
        return SimpleNamespace(values={'events': [], 'messages': []},
                               tasks=[SimpleNamespace(interrupts=[interrupt])])


def make_state(graph, runtime=None):
    return SimpleNamespace(
        graph=graph, checkpointer=object(), settings=SettingsStub(),
        summary_manager=None, thread_runtime=runtime if runtime is not None else {},
    )


def make_request(app_state):
    return SimpleNamespace(
        state=SimpleNamespace(user_id=1, request_id='rid-test'),
        app=SimpleNamespace(state=app_state),
    )


def locked_runtime(pending=()):
    return {'active': True, 'pending': deque(pending), 'waiting_approval': True}


def rt_of(app_state, key):
    return get_runtime(app_state)[key]


@pytest.mark.asyncio
async def test_chat_setup_failure_releases_lock_and_next_turn_works():
    """端点读 checkpoint 抛异常 -> 锁释放，且下一轮能正常发起 run，而不是 queued。"""
    app_state = make_state(BrokenGraph())
    request = make_request(app_state)
    with pytest.raises(RuntimeError):
        await chat(ChatRequest(message='查一下餐饮', session_id='s1'), request)
    rt = rt_of(app_state, 'u1:s1')
    assert rt['active'] is False
    assert rt['waiting_approval'] is False

    app_state.graph = EmptyGraph()
    result = await chat(ChatRequest(message='再问一次', session_id='s1'), request)
    assert isinstance(result, StreamingResponse)
    # 正常路径仍然持锁，等 runner 的 finally 释放（不能被这次修复误伤）
    assert rt['active'] is True


@pytest.mark.asyncio
async def test_chat_cancelled_releases_lock():
    """客户端断连抛 CancelledError（BaseException 子类）也必须释放锁。"""
    app_state = make_state(BrokenGraph(asyncio.CancelledError()))
    request = make_request(app_state)
    with pytest.raises(asyncio.CancelledError):
        await chat(ChatRequest(message='查一下餐饮', session_id='s2'), request)
    assert rt_of(app_state, 'u1:s2')['active'] is False


@pytest.mark.asyncio
async def test_resume_read_failure_releases_lock():
    """resume 读 checkpoint 失败：审批锁同样要释放。"""
    key = 'u1:s3'
    app_state = make_state(BrokenGraph(), {key: locked_runtime()})
    with pytest.raises(RuntimeError):
        await resume(key, ResumeRequest(approval_id='ap-1', decision='approve'),
                     make_request(app_state))
    assert rt_of(app_state, key)['active'] is False


@pytest.mark.asyncio
async def test_resume_missing_run_releases_lock():
    """运行已不存在（404）：没有 resume 会来接管，必须释放。"""
    key = 'u1:s4'
    app_state = make_state(EmptyGraph(), {key: locked_runtime()})
    with pytest.raises(HTTPException) as exc:
        await resume(key, ResumeRequest(approval_id='ap-1', decision='approve'),
                     make_request(app_state))
    assert exc.value.status_code == 404
    rt = rt_of(app_state, key)
    assert rt['active'] is False
    assert rt['waiting_approval'] is False


@pytest.mark.asyncio
async def test_expired_approve_returns_410_and_keeps_lock():
    """过期审批不允许 approve（防止延迟写），且刻意保留锁。

    只释放 runtime 锁会造成假恢复：checkpoint 里的 interrupt 仍在，下一条 /chat
    会立刻重抛 __interrupt__，模型根本不执行。正确自愈路径是点「拒绝」。
    """
    key = 'u1:s5'
    app_state = make_state(
        ApprovalGraph(created_at='2020-01-01T00:00:00+00:00'),
        {key: locked_runtime(pending=['排队中的追加'])},
    )
    with pytest.raises(HTTPException) as exc:
        await resume(key, ResumeRequest(approval_id='ap-1', decision='approve'),
                     make_request(app_state))
    assert exc.value.status_code == 410
    rt = rt_of(app_state, key)
    assert rt['active'] is True
    assert rt['waiting_approval'] is True
    assert list(rt['pending']) == ['排队中的追加']


@pytest.mark.asyncio
async def test_expired_reject_resumes_and_clears_waiting():
    """过期审批允许 reject 放行：图走完挂起节点，runner 的 finally 负责复位锁。"""
    key = 'u1:s5b'
    app_state = make_state(
        ApprovalGraph(created_at='2020-01-01T00:00:00+00:00'),
        {key: locked_runtime()},
    )
    result = await resume(key, ResumeRequest(approval_id='ap-1', decision='reject'),
                          make_request(app_state))
    assert isinstance(result, StreamingResponse)
    rt = rt_of(app_state, key)
    assert rt['active'] is True           # 交给 runner，不提前解锁
    assert rt['waiting_approval'] is False


@pytest.mark.asyncio
async def test_expired_approve_then_reject_recovers():
    """完整自愈链：approve 被 410 拒绝 -> 前端恢复卡片 -> 点 reject 放行。"""
    key = 'u1:s5c'
    app_state = make_state(
        ApprovalGraph(created_at='2020-01-01T00:00:00+00:00'),
        {key: locked_runtime()},
    )
    request = make_request(app_state)
    with pytest.raises(HTTPException) as exc:
        await resume(key, ResumeRequest(approval_id='ap-1', decision='approve'), request)
    assert exc.value.status_code == 410
    assert rt_of(app_state, key)['active'] is True

    result = await resume(key, ResumeRequest(approval_id='ap-1', decision='reject'), request)
    assert isinstance(result, StreamingResponse)
    assert rt_of(app_state, key)['waiting_approval'] is False


@pytest.mark.asyncio
async def test_resume_wrong_approval_id_keeps_lock():
    """409（传错 approval_id）：运行仍在等待另一个审批，刻意不解锁，允许用正确 id 重试。"""
    key = 'u1:s6'
    app_state = make_state(ApprovalGraph(approval_id='ap-real'), {key: locked_runtime()})
    with pytest.raises(HTTPException) as exc:
        await resume(key, ResumeRequest(approval_id='ap-wrong', decision='approve'),
                     make_request(app_state))
    assert exc.value.status_code == 409
    rt = rt_of(app_state, key)
    assert rt['active'] is True
    assert rt['waiting_approval'] is True


@pytest.mark.asyncio
async def test_resume_success_keeps_lock_for_runner():
    """正常 resume：锁交给 runner 的 finally 释放，端点不提前解锁。"""
    key = 'u1:s7'
    app_state = make_state(ApprovalGraph(), {key: locked_runtime()})
    result = await resume(key, ResumeRequest(approval_id='ap-1', decision='approve'),
                          make_request(app_state))
    assert isinstance(result, StreamingResponse)
    rt = rt_of(app_state, key)
    assert rt['active'] is True
    assert rt['waiting_approval'] is False
