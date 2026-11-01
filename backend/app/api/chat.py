import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.agent.graph import initial_input
from app.observability import langfuse_callbacks
from app.schemas import ChatRequest, ResumeRequest

router = APIRouter(prefix='/api', tags=['chat'])

# 模型生成期间的心跳文案（轮换，避免长时间"卡住"的观感）
HEARTBEAT_TEXT = ['正在思考…', '正在分析账单…', '正在组织回答…']

# 续轮输入的历史保留上限：防止单会话上下文无限增长导致模型超限
MAX_HISTORY_MESSAGES = 30


def get_runtime(app_state) -> dict:
    """获取某 thread 的运行时状态（单进程内存）：active=执行中 / pending=追加输入队列 /
    waiting_approval=审批挂起。检查点注入（执行中可追加任务）依赖它：
    用户在执行中发送的消息进 pending 队列，循环 runner 在每个输入检查点消费并注入。"""
    rt = getattr(app_state, 'thread_runtime', None)
    if rt is None:
        rt = {}
        app_state.thread_runtime = rt
    return rt


def _thread_runtime(app_state, thread_id: str) -> dict:
    rt = get_runtime(app_state)
    if thread_id not in rt:
        rt[thread_id] = {'active': False, 'pending': deque(), 'waiting_approval': False}
    return rt[thread_id]


def _repair_pairs(msgs):
    """工具调用配对修复：丢弃孤立的 ToolMessage 与未配对完整的 AI(tool_calls)，
    保证发给模型的每条 tool 消息都有前置 tool_calls、每个 tool_calls 都有对应响应。"""
    # pass1：丢弃开头/中间孤立的 ToolMessage（截断可能切掉其前置 AI(tool_calls)）
    pending = 0
    p1 = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            if pending <= 0:
                continue
            p1.append(m)
            pending -= 1
        else:
            p1.append(m)
            calls = getattr(m, 'tool_calls', None) or []
            if calls:
                pending += len(calls)
    # pass2：丢弃未配对完整的 AI(tool_calls)（含其部分 Tool 段，避免残留孤儿），
    # 并裁剪多余的 Tool（OpenAI 要求每个 tool 消息响应一个 tool_call）
    result = []
    i = 0
    while i < len(p1):
        m = p1[i]
        calls = getattr(m, 'tool_calls', None) or []
        if not calls:
            result.append(m)
            i += 1
            continue
        j = i + 1
        while j < len(p1) and isinstance(p1[j], ToolMessage):
            j += 1
        responded = j - i - 1
        if responded < len(calls):
            i = j
            continue
        result.append(m)
        result.extend(p1[i + 1:i + 1 + len(calls)])
        i = j
    return result


def _trim_history(messages):
    """截断续轮输入的历史：清理异常消息（残缺流式 chunk、失败自愈提示），
    保留 System 前缀 + 最近 N 条，并修复截断产生的孤立工具消息，
    控制模型上下文规模与合法性。"""
    clean = []
    for m in messages:
        # 残缺的流式工具调用：tool_call_chunks 未解析成完整 tool_calls 且无后续结果 → 丢弃
        if isinstance(m, AIMessageChunk) and getattr(m, 'tool_call_chunks', None) \
                and not getattr(m, 'tool_calls', None):
            continue
        # 失败自愈提示（旧版本写入历史的污染）→ 丢弃
        if isinstance(m, SystemMessage) and '模型调用失败' in str(m.content):
            continue
        clean.append(m)
    # 截断：保留 System + 最近 N 条；首条 Human（初始任务目标）永不截断——
    # 任务型对话的目标一旦被截掉，模型后续会忘记最初目的（多步任务漂移）
    if len(clean) > MAX_HISTORY_MESSAGES:
        first_human = next((m for m in clean if isinstance(m, HumanMessage)), None)
        if isinstance(clean[0], SystemMessage):
            tail = clean[-(MAX_HISTORY_MESSAGES - 1):]
            if first_human is not None and not any(m is first_human for m in tail):
                tail = [first_human, *tail[:MAX_HISTORY_MESSAGES - 2]]
            clean = [clean[0], *tail]
        else:
            clean = clean[-MAX_HISTORY_MESSAGES:]
    # 工具调用配对修复（截断与历史残缺都可能产生孤立/未配对消息）
    return _repair_pairs(clean)


def _sse(run_id, event):
    return f"data: {json.dumps({'run_id': run_id, **event}, ensure_ascii=False)}\n\n"


def stream_graph(graph, graph_input, run_id, checkpointer, seen_start=0, max_steps=None,
                 runtime=None, run_timeout=None, max_tool_calls=None):
    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()

        # 流式出口：agent 节点内 astream 的 token 增量经此实时入队
        async def stream_sink(text: str) -> None:
            queue.put_nowait(_sse(run_id, {'type': 'stream', 'payload': {'delta': text}}))

        config = {'configurable': {'thread_id': run_id, 'stream_sink': stream_sink}}
        # Langfuse 追踪：handler 放在 RunnableConfig 里，LangGraph 会自动沿调用链
        # 下传给每个节点内的 LLM 与工具调用，不需要去 graph.py 逐节点埋点。
        # 未启用时是空列表，此时干脆不写这个键，避免给 langgraph 传空 callbacks。
        callbacks = langfuse_callbacks()
        if callbacks:
            config['callbacks'] = callbacks
        # 步数护栏：langgraph 1.x 在运行时通过 config 传 recursion_limit，
        # 超限抛 GraphRecursionError（下方转友好 final），防止模型陷入工具循环停不下来
        if max_steps:
            config['recursion_limit'] = max_steps
        # 工具调用次数硬护栏（成本护栏）：经 configurable 传给 agent 节点，
        # 由 state.tool_calls_done 跨段/跨审批累计检查（子图内循环不产生顶层事件推送，
        # 不能在 runner 层仅靠事件计数）
        if max_tool_calls:
            config['configurable']['max_tool_calls'] = max_tool_calls

        # 心跳：模型思考/生成期间每 2.5s 推一条 progress，让前端始终有反馈
        async def heartbeat():
            i = 0
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.5)
                    return
                except asyncio.TimeoutError:
                    if not stop.is_set():
                        queue.put_nowait(_sse(run_id, {'type': 'progress', 'payload': {
                            'text': HEARTBEAT_TEXT[i % len(HEARTBEAT_TEXT)],
                        }}))
                        i += 1

        # 图执行：循环段式 runner（检查点注入的核心）。
        # 一次 astream 跑到 interrupt 或 END 即为一"段"；段间处理 interrupt：
        # - tool_approval → 推 approval_created 后停住（等 /runs/{id}/resume，不再自动继续）
        # - input_checkpoint → 消费 thread pending 队列：有输入则 Command(resume=新输入) 继续
        #   （不重跑已完成步骤，checkpoint 保证历史在），无输入则 resume(None) 自动放行。
        # 因此「执行中追加任务」在下一轮即生效，同时仍满足串行写 checkpoint（无并发执行流）。
        # 运行时治理：
        # - run_timeout：整段 run 的墙钟时间上限（asyncio.timeout），超时推友好 final 安全停止
        #   （已完成步骤已 checkpoint 落盘，不丢进度）；
        # - max_tool_calls：工具调用硬上限（成本护栏），跨审批续跑用 thread_runtime 累计不重置。
        async def runner():
            seen = seen_start
            last_final = None
            current_input = graph_input
            loop_count = 0
            tool_calls_done = 0
            max_loops = max_steps if max_steps else 8

            async def run_segments():
                nonlocal seen, last_final, current_input, loop_count, tool_calls_done
                while True:
                    if loop_count >= max_loops:
                        queue.put_nowait(_sse(run_id, {'type': 'final', 'payload': {
                            'text': f'这次任务执行的步骤超过了上限（{max_loops} 轮），已安全停止。'
                                    '建议把任务拆小一点，或换一种更简洁的说法重试。',
                        }}))
                        return
                    interrupt_info = None
                    try:
                        async for chunk in graph.astream(current_input, config, stream_mode='updates'):
                            for node_name, payload in chunk.items():
                                if node_name == '__interrupt__':
                                    for item in payload:
                                        value = item.value
                                        if not isinstance(value, dict):
                                            continue
                                        kind = value.get('kind')
                                        if kind == 'tool_approval':
                                            interrupt_info = ('approval', value)
                                        elif kind == 'input_checkpoint':
                                            interrupt_info = ('input', value)
                                    continue
                                # updates 模式下节点返回完整累积 events，用全局 seen 去重推送增量；
                                # 注意无 events 的节点（approve/agent）不得把 seen 重置为 0
                                events = payload.get('events') or []
                                for event in events[seen:]:
                                    queue.put_nowait(_sse(run_id, event))
                                    # 工具调用次数护栏：优先用 thread_runtime 跨段累计
                                    # （审批续跑不重置），无 runtime（单测直调）退化局部计数
                                    if event.get('type') == 'tool_result':
                                        if runtime is not None:
                                            runtime['tool_calls'] = runtime.get('tool_calls', 0) + 1
                                        else:
                                            tool_calls_done += 1
                                if events:
                                    seen = len(events)
                                if payload.get('final') and payload['final'] != last_final:
                                    last_final = payload['final']
                                    queue.put_nowait(_sse(run_id, {'type': 'final',
                                                                   'payload': {'text': payload['final']}}))
                                calls_now = (runtime.get('tool_calls', 0)
                                             if runtime is not None else tool_calls_done)
                                if max_tool_calls and calls_now >= max_tool_calls:
                                    queue.put_nowait(_sse(run_id, {'type': 'final', 'payload': {
                                        'text': f'这次任务执行的工具调用次数超过了上限（{max_tool_calls} 次），'
                                                '已安全停止；可把任务拆小或换个更简洁的说法重试。',
                                    }}))
                                    return
                    except GraphRecursionError:
                        # 步数护栏触发：模型反复调用工具停不下来 → 转成友好 final，不当作崩溃
                        limit = max_steps if max_steps else '上限'
                        queue.put_nowait(_sse(run_id, {'type': 'final', 'payload': {
                            'text': f'这次任务执行的步骤超过了上限（{limit} 步），已安全停止。'
                                    '建议把任务拆小一点，或换一种更简洁的说法重试。',
                        }}))
                        return
                    except Exception as exc:
                        queue.put_nowait(_sse(run_id, {'type': 'error',
                                                       'payload': {'code': 'RUN_FAILED',
                                                                   'error': str(exc)}}))
                        return
                    if interrupt_info is None:
                        return  # 图自然跑到 END
                    kind, value = interrupt_info
                    if kind == 'approval':
                        if runtime is not None:
                            runtime['waiting_approval'] = True  # 保持 active，等 resume 端点接管
                        event = {'type': 'approval_created', 'payload': {
                            'approval_id': value['approval_id'], 'tool': value['tool'],
                            'args': value['args'], 'message': value['message'],
                        }}
                        queue.put_nowait(_sse(run_id, event))
                        return  # 本轮 SSE 结束；审批期间用户新输入继续入队，resume 后注入
                    # input_checkpoint：消费追加输入队列，逐轮注入（Codex 风格执行中追加）
                    loop_count += 1
                    new_input = None
                    if runtime is not None and runtime.get('pending'):
                        new_input = runtime['pending'].popleft()
                    current_input = Command(resume={'new_input': new_input})

            try:
                if run_timeout:
                    async with asyncio.timeout(run_timeout):
                        await run_segments()
                else:
                    await run_segments()
            except TimeoutError:
                # run 墙钟超时：已完成步骤已 checkpoint 落盘，推友好 final 而非崩溃
                queue.put_nowait(_sse(run_id, {'type': 'final', 'payload': {
                    'text': f'这次任务执行超过了时间上限（{run_timeout} 秒），已安全停止；'
                            '已完成部分已保存，可继续追加提问或换个说法重试。',
                }}))
            finally:
                stop.set()
                queue.put_nowait('__DONE__')
                # 释放 thread 运行时状态：审批挂起时保持 active（用户输入继续入队，等 resume），
                # 否则释放，允许下一次 /chat 正常发起新 run
                if runtime is not None and not runtime.get('waiting_approval'):
                    runtime['active'] = False

        hb_task = asyncio.create_task(heartbeat())
        runner_task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item == '__DONE__':
                    break
                yield item
        finally:
            stop.set()
            await hb_task
            if not runner_task.done():
                runner_task.cancel()
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream')


@router.post('/chat')
async def chat(payload: ChatRequest, request: Request):
    """多轮会话：同一 session_id 复用 thread，消息追加进 checkpoint 历史实现上下文衔接；
    首轮注入 System+Human，续轮只追加 Human。
    执行中追加（检查点注入）：thread 正忙时新消息进入 pending 队列，返回 {queued:true}；
    循环 runner 在下个输入检查点把它注入并继续，无需重跑已完成步骤。"""
    thread_id = payload.session_id or uuid4().hex
    graph = request.app.state.graph
    rt = _thread_runtime(request.app.state, thread_id)
    if rt['active']:
        # 已有执行流在跑：入队，让当前 run 的 runner 在输入检查点消费（串行，不并发写 checkpoint）
        rt['pending'].append(payload.message)
        return {'queued': True,
                'message': '已加入队列，将在当前任务的下一个检查点追加。'}
    rt['active'] = True
    rt['waiting_approval'] = False
    rt['tool_calls'] = 0  # 新 run 重置工具调用计数（审批续跑在 resume 端点保留，累计同一任务成本）
    config = {'configurable': {'thread_id': thread_id}}
    snapshot = await graph.aget_state(config)
    seen_start = 0
    if snapshot.values.get('messages'):
        # 续轮：显式携带历史（AgentState.messages 无 add_messages reducer，输入会整体替换而非追加）
        history = _trim_history(snapshot.values['messages'])
        messages = [*history, HumanMessage(content=payload.message)]
        seen_start = len(snapshot.values.get('events', []))
    else:
        messages = initial_input(thread_id, payload.message)['messages']
    # 合并上一轮 END 竞态残留的排队输入（时序上晚于最后一个输入检查点到达的消息）
    leftovers = list(rt['pending'])
    rt['pending'].clear()
    for text in leftovers:
        messages.append(HumanMessage(content=text))
    graph_input = {'messages': messages}
    settings = getattr(request.app.state, 'settings', None)
    return stream_graph(graph, graph_input, thread_id, request.app.state.checkpointer,
                        seen_start=seen_start, runtime=rt,
                        max_steps=getattr(settings, 'max_steps', None),
                        run_timeout=getattr(settings, 'run_timeout_seconds', None),
                        max_tool_calls=getattr(settings, 'max_tool_calls_per_run', None))


@router.post('/runs/{run_id}/resume')
async def resume(run_id: str, payload: ResumeRequest, request: Request):
    graph = request.app.state.graph
    config = {'configurable': {'thread_id': run_id}}
    snapshot = await graph.aget_state(config)
    if not snapshot.values:
        raise HTTPException(404, '运行不存在')
    pending = False
    created_at = None
    for task in snapshot.tasks:
        for inter in task.interrupts:
            value = inter.value
            if isinstance(value, dict) and value.get('approval_id') == payload.approval_id:
                pending = True
                created_at = value.get('created_at')
    if not pending:
        raise HTTPException(409, '运行未等待此审批')
    settings = getattr(request.app.state, 'settings', None)
    # 审批 TTL：审批创建后超过 approval_ttl_seconds 未处理视为过期，拒绝恢复并提示重新发起
    ttl = getattr(settings, 'approval_ttl_seconds', None)
    if ttl and created_at:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).total_seconds()
        except (ValueError, TypeError):
            age = 0
        if age > ttl:
            raise HTTPException(410, f'审批已过期（超过 {ttl} 秒），请重新发起该操作')
    decision = {'decision': payload.decision, 'approval_id': payload.approval_id}
    if payload.edited_args is not None:
        decision['edited_args'] = payload.edited_args
    seen_start = len(snapshot.values.get('events', [])) if snapshot.values else 0
    # 审批期间用户追加的输入在 pending 队列里，resume 后由循环 runner 在下一检查点注入
    rt = _thread_runtime(request.app.state, run_id)
    rt['active'] = True
    rt['waiting_approval'] = False
    # 注意：不重置 rt['tool_calls']——审批续跑与同一任务累计成本，工具调用次数护栏跨段生效
    return stream_graph(graph, Command(resume=decision), run_id, request.app.state.checkpointer,
                        seen_start=seen_start, runtime=rt,
                        max_steps=getattr(settings, 'max_steps', None),
                        run_timeout=getattr(settings, 'run_timeout_seconds', None),
                        max_tool_calls=getattr(settings, 'max_tool_calls_per_run', None))
