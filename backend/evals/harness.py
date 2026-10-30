"""单用例执行器：把一条用例真正跑过一遍 Agent 图，采集可判定的观测值。

刻意走**生产同一条链路**（`make_graph` + 真实工具 + MemorySaver），而不是单独调模型：
评估要测的是「系统」的行为，不是「模型裸调」的行为。审批、拦截、上下文卫生这些护栏
一旦被绕过，指标里必须能看出来。

隔离策略：每条用例一个临时 SQLite 库 + 一个独立 thread_id，
用完即删。用例之间不会互相污染，也不会碰到真实账单库。
"""
import asyncio
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import select

from app.agent.graph import initial_input, make_graph
from app.agent.tools import OUTPUT_SCHEMAS, WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import AgentMemory, Bill, ScheduleEvent, TrackingCard
from evals.dataset import SEEDS, Case

# 单条用例最多自动批准几次写操作；超限即拒绝，防止模型自激循环把评估跑成无底洞
MAX_AUTO_APPROVALS = 4


@dataclass
class TurnRun:
    """一轮对话的观测值。"""

    index: int
    proposed: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    final: str = ''
    error: str | None = None
    messages: list[Any] = field(default_factory=list)

    @property
    def proposed_tools(self) -> list[str]:
        return [p['tool'] for p in self.proposed]


@dataclass
class CaseRun:
    case_id: str
    turns: list[TurnRun] = field(default_factory=list)
    latency_ms: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None

    @property
    def last(self) -> TurnRun:
        return self.turns[-1]


async def _seed(db: Database, seed_names: tuple[str, ...]) -> None:
    async with db.sessions() as session:
        async with session.begin():
            for name in seed_names:
                for idx, row in enumerate(SEEDS[name], start=1):
                    if name == 'bills':
                        session.add(Bill(
                            merchant_tx_no=f"m{row['tx_no']}", note='', source='eval',
                            import_batch_id='eval', source_sheet='Sheet1', source_row=idx, **row))
                    elif name == 'cards':
                        session.add(TrackingCard(invocation_id=uuid4().hex, note='', **row))
                    elif name == 'events':
                        session.add(ScheduleEvent(invocation_id=uuid4().hex, **row))
                    else:  # pragma: no cover - 数据集里不会出现未知种子
                        raise KeyError(name)


def _memory_loader(db: Database):
    async def load() -> str | None:
        async with db.sessions() as session:
            rows = list(await session.scalars(
                select(AgentMemory).order_by(AgentMemory.updated_at.desc())))
        if not rows:
            return None
        return '；'.join(f'{r.key}={r.value}' for r in rows)

    return load


async def _drive(graph, payload, config, timeout: float, turn: TurnRun) -> dict[str, Any]:
    """执行到「无 interrupt」或「等待审批」为止；遇到输入检查点自动放行。

    审批一律自动通过——要测的是「审批有没有被要求」，不是「用户点了什么」。
    如果写操作绕过了审批，approvals 会是空的，指标里能立刻看出来。
    """
    current = payload
    while True:
        async with asyncio.timeout(timeout):
            result = await graph.ainvoke(current, config)
        interrupts = result.get('__interrupt__') or []
        if not interrupts:
            return result
        value = getattr(interrupts[0], 'value', interrupts[0])
        if isinstance(value, dict) and value.get('kind') == 'tool_approval':
            turn.approvals.append({'tool': value.get('tool'), 'args': value.get('args'),
                                   'approval_id': value.get('approval_id')})
            approved = len(turn.approvals) <= MAX_AUTO_APPROVALS
            current = Command(resume={'decision': 'approve' if approved else 'reject',
                                      'approval_id': value.get('approval_id')})
        else:
            current = Command(resume={'new_input': None})


def _collect(turn: TurnRun, result: dict[str, Any]) -> None:
    for event in result.get('events') or []:
        kind = event.get('type')
        payload = event.get('payload') or {}
        if kind == 'tool_call_proposal':
            turn.proposed.append({'tool': payload.get('tool'), 'args': payload.get('args') or {}})
        elif kind == 'tool_result':
            envelope = payload.get('result') or {}
            turn.results.append({
                'tool': payload.get('tool'),
                'ok': bool(envelope.get('ok')),
                'error_code': (envelope.get('error') or {}).get('code'),
            })
    turn.final = result.get('final') or turn.final
    turn.messages = result.get('messages') or turn.messages


def _tokens(messages: list[Any]) -> tuple[int | None, int | None]:
    tin = tout = None
    for msg in messages:
        usage = getattr(msg, 'usage_metadata', None)
        if not usage:
            continue
        tin = (tin or 0) + int(usage.get('input_tokens') or 0)
        tout = (tout or 0) + int(usage.get('output_tokens') or 0)
    return tin, tout


async def run_case(case: Case, model, *, timeout: float = 120.0) -> CaseRun:
    """跑一条用例。异常不抛出，转成 CaseRun.error，让整批评估不会因为一题崩掉而中断。"""
    run = CaseRun(case_id=case.id)
    tmp = Path(tempfile.mkdtemp(prefix=f'eval-{case.id}-'))
    db = Database(tmp / 'eval.db')
    started = time.perf_counter()
    try:
        await db.initialize()
        await _seed(db, case.seeds)
        graph = make_graph(
            model, build_tools(db), WRITE_TOOLS, MemorySaver(),
            memory_loader=_memory_loader(db),
            tool_timeout=15, model_retries=0, retry_backoff=0.1,
            tool_retries=0, output_schemas=OUTPUT_SCHEMAS,
            context_max_chars=2000, context_max_lines=40,
        )
        config = {'configurable': {'thread_id': f'eval-{case.id}-{uuid4().hex[:8]}'},
                  'recursion_limit': 24}
        history: list[Any] = []
        for index, text in enumerate(case.turns):
            turn = TurnRun(index=index)
            if index == 0:
                payload = initial_input(config['configurable']['thread_id'], text)
            else:
                # 续轮：显式携带历史（AgentState.messages 无 add_messages reducer），
                # 与 chat.py 的行为一致；intent 节点会重建 system。
                payload = {'messages': [*history, HumanMessage(content=text)], 'events': []}
            try:
                result = await _drive(graph, payload, config, timeout, turn)
                _collect(turn, result)
            except Exception as exc:  # noqa: BLE001 - 单题失败不应中断整批
                turn.error = f'{exc.__class__.__name__}: {exc}'
                run.error = turn.error
            run.turns.append(turn)
            history = [m for m in turn.messages if not isinstance(m, SystemMessage)]
            if turn.error:
                break
        tin, tout = _tokens(history)
        run.tokens_in, run.tokens_out = tin, tout
    except Exception as exc:  # noqa: BLE001 - 建库/播种失败同样降级为单题失败
        run.error = f'{exc.__class__.__name__}: {exc}'
    finally:
        run.latency_ms = (time.perf_counter() - started) * 1000
        await db.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return run
