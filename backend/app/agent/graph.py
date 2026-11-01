"""LangGraph Supervisor 架构：intent 领域路由 →（账单子图 | 通用 Agent）→ 输入检查点 → END。

治理点：
- 提示词分层：铁律常驻（CORE_RULES）+ 流程 Skill 按需注入（intent 节点按用户意图判定，
  把对应 PROCESS_SKILLS 拼进 system；无关轮次只带铁律，瘦身上下文、聚焦注意力）。
- 多 Agent 协作（Supervisor 模式）：intent 节点升级为领域路由（detect_agent），
  账单域请求进入 bill_subgraph（独立子图：bill_agent → bill_approve/bill_tools 循环），
  闲聊/兜底走通用 agent 节点；interrupt（审批/输入检查点）只在 Supervisor 层，
  子图内不设输入检查点，减少中断次数；追加输入经 check_input 后回 intent 重新路由。
- HITL：写工具（WRITE_TOOLS）在进入 tools 前经 approve 节点 interrupt（子图内同样适用，
  interrupt 全局冒泡到顶层调用者），前端拿到 approval_created 事件弹确认卡，
  resume(approve/reject) 后继续或回退。
- HITL 不变量（并行调用）：一条 AIMessage 内的并行工具调用，只允许「全部只读」或
  「仅含一个写工具」。路由节点按 calls[0] 决定是否审批，若 run_tools 遍历全部调用，
  「读 + 写」混合批会让写操作绕过审批直接落库；因此 run_tools 对混合批只执行第一个
  调用，其余以 PARALLEL_WRITE_BLOCKED 回执退回模型，令其逐个重发。
- 事件协议：与旧版前端完全兼容——节点把事件增量写入 state['events']，
  chat.py 用 stream_mode='updates' 增量推送；interrupt 由 chat.py 转 approval_created。
- schema 校验：工具调用由 args_schema 强校验，失败转结构化 validation 错误回喂模型。
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from app.agent.tools import _IDEMPOTENCY_KEY
from app.data.models import now_iso
from app.errors import ToolError

# ---------- 提示词分层：铁律常驻 + 流程 Skill 按需注入 ----------

CORE_RULES = (
    '你是个人账单管家。只使用已注册工具。铁律：\n'
    '1. 组合任务按顺序执行：先查询（只读），拿到结果后基于结果继续写操作（建卡），'
    '一次回复即可走完「查询→建卡」完整链路，不再反问「是否继续」。\n'
    '2. 默认行动优先于反问：能从上下文推断的参数用推断值，并在回复中说明假设；'
    '只有无法行动的硬缺失（如账单 ID 尚未查询）才澄清，且一次只问一组问题、给出候选。\n'
    '3. 写操作（建卡/完成/批量）都会经过人工确认；不得编造工具名、参数、账单 ID、卡片 ID。\n'
    '4. 创建卡片必须是用户明确要求，不可把报销/退订咨询当作创建指令。\n'
    '5. 长期记忆（save_memory）只保存用户明确表达的偏好/事实（「以后…」「默认…」「xx是…」），'
    '不得推测、归纳或编造。\n'
    '6. 写操作必须单独提交：一条响应里只能有一个工具调用是写操作，查询与建卡分成两次响应'
    '（先查、看到结果再建）；需要为多笔记录建卡时用 create_cards_from_query 一次完成，'
    '不要拆成多次 create_tracking_card。\n'
    '当前日期(Asia/Shanghai)：{date}'
)

# 流程 Skill：仅在用户意图命中时注入（按需加载），避免 15 条细节规则常驻稀释注意力
PROCESS_SKILLS = {
    'skill_query': (
        '【流程·查询账单】\n'
        '- 查给某人的转账：merchant=姓名原文，transaction_type=转账，direction=expense；'
        '收到某人转账：direction=income；双方往来或未指定方向：direction=null。\n'
        '- 查付款/转账历史是只读查询，不是发起转账；仅支持历史查询和创建待办，'
        '不支持实际转账、付款或退款。\n'
        '- 商户默认按名称包含匹配，要求完全一致则 merchant_match=exact。\n'
        '- 大于/超过100元：min_amount=100 且 min_amount_inclusive=false；至少100元保持 true。\n'
        '- 金额单位为元，最多两位小数；日期明确到某天时 start_date=end_date。\n'
        '- 未说明方向默认支出，但查看全部收支或退款记录应 direction=null。\n'
        '- 用户说「只要…」「再筛…」「加上…」等补充条件时，默认在【当前对话记忆】标明的'
        '最近查询范围内二次筛选；只有用户明确要求新的时间范围或重新查询时才发起新的全量查询。'
    ),
    'skill_create': (
        '【流程·创建卡片】\n'
        '- 卡片用途未指定时默认「报销」，方向未指定时默认支出 expense，并在回复中说明。\n'
        '- 消费分类有餐饮、交通、购物、订阅、医疗、住房、教育、其他；交易类型不等于分类。\n'
        '- 建卡目标：用户要求把转账/某类记录做成卡片时先查询相关记录；结果恰一笔则直接建卡；'
        '多笔时默认对最近一笔建卡并在回复中说明可更换；用户明确「全部/每笔」时全建。\n'
        '- 批量建卡（多笔记录都要建卡）用 create_cards_from_query，不要逐张 create_tracking_card'
        '（省模型往返与审批次数）；交易属性（对方/金额/时间/方向）自动从账单快照复制。\n'
        '- 卡片分类：跟随账单已有分类（「根据账单信息放不同区域」）用 use_bill_category=True；'
        '各张按对方/类型不同分类用 category_rules（如「张三→餐饮、李四→交通」）；'
        '全部同分类直接用 card_category。'
    ),
    'skill_reimburse': (
        '【流程·报销/AA/退订】\n'
        '- 用途体系：报销（默认）、退款、待退订、AA、待处理；AA 用于多人分摊场景。\n'
        '- 「报销」指把消费记录做成报销待办卡片；不要把「能否报销/怎么退订」的咨询当作创建卡片指令。\n'
        '- 完成报销/退订动作：把对应卡片用 complete_tracking_card 移到已完成，卡片 ID 必须来自查询结果。'
    ),
    'skill_board': (
        '【流程·看板操作】\n'
        '- 用户要求「把某张卡片移到已完成/标记完成」时用 complete_tracking_card；'
        '卡片 ID 必须来自 list_tracking_cards 或最近查询结果，不得编造。\n'
        '- 查看看板/泳道/任务列表用 list_tracking_cards；卡片按状态分 active（待办）与 done（已完成）。'
    ),
    'skill_search': (
        '【流程·搜索】\n'
        '- 需要外部实时信息（新闻/攻略/天气/价格/评测/路线等本地账单里没有的资料）时用 web_search，'
        '一次调用即可，结果会整理成要点。\n'
        '- 账单/收支/卡片类查询先查本地账单（query_bill），不要用 web_search。\n'
        '- 搜索结果整理成简洁要点并注明来源；信息有时效，过时或来源不明需如实说明。'
    ),
    'skill_schedule': (
        '【流程·日程】\n'
        '- 查日程用 list_events（默认只看未完成，可带日期范围/status）；'
        '新建用 create_event、改期用 update_event、标记完成用 complete_event，写操作都会经用户确认。\n'
        '- 时间格式：YYYY-MM-DD（全天）或 YYYY-MM-DDTHH:MM（精确到分）；'
        '「明天/下周一」等相对时间先按当前日期换算成具体日期再调用。\n'
        '- 日程 ID 必须来自查询结果，不得编造。'
    ),
}

# 意图关键词 → 流程 Skill；命中即注入（一次可注入多个，按命中顺序）
SKILL_KEYWORDS = {
    'skill_query': ('账单', '转账', '收支', '流水', '明细', '消费', '记录', '花了', '支出', '收入', '餐饮', '购物', '交通'),
    'skill_create': ('建卡', '做成卡片', '生成卡片', '建卡片', '待办', '卡片', '创建'),
    'skill_reimburse': ('报销', 'AA', '待退订', '退订', '退款'),
    'skill_board': ('看板', '已完成', '移动卡片', '泳道', '任务列表'),
    'skill_search': ('搜索', '查一下', '查查', '最新', '新闻', '攻略', '天气', '旅游', '路线', '评测', '行情', '资讯',
                     '推荐', '怎么样', '如何'),
    'skill_schedule': ('日程', '日历', '会议', '提醒', '预约', '安排', '改期', '延期'),
}


def detect_skills(message: str) -> list[str]:
    """轻量意图预判：命中关键词就注入对应流程 Skill（顺序保留，可能多个）。"""
    hits = []
    for skill, words in SKILL_KEYWORDS.items():
        if any(w in message for w in words):
            hits.append(skill)
    return hits


_BILL_SKILLS = ('skill_query', 'skill_create', 'skill_reimburse', 'skill_board')


def detect_agent(skills: list[str]) -> str:
    """领域路由（Supervisor）：账单域优先 → 日程域 → 搜索域 → 通用兜底。
    - 命中任一账单流程 Skill → 'bill'（下发账单子图）；
    - 仅命中日程 Skill → 'schedule'（下发日程子图）；
    - 仅命中搜索 Skill → 'search'（下发搜索子图）；
    - 未命中任何 Skill → 'general'（通用 agent 兜底）。
    「查一下天气」→ search；「查一下明天的日程」→ schedule；「查8月账单」→ bill。"""
    if any(s in _BILL_SKILLS for s in skills):
        return 'bill'
    if 'skill_schedule' in skills:
        return 'schedule'
    if 'skill_search' in skills:
        return 'search'
    return 'general'


def build_system_message(skills: list[str], memory_text: str | None = None) -> str:
    """组装 system：铁律常驻 + 命中 Skill 按需注入 + 长期记忆全量注入。
    memory_text 为跨会话持久的事实/偏好（agent_memory 表全量），规模小时全量注入优于检索。"""
    date_text = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    parts = [CORE_RULES.replace('{date}', date_text)]
    if memory_text:
        parts.append('【长期记忆】以下是你已了解的该用户的事实与偏好（来自以往明确表达，'
                     '除非用户最新指令与之冲突，否则按此执行）：\n' + memory_text)
    if skills:
        parts.append('以下为本次任务相关流程，严格按步骤执行：')
        for name in skills:
            if name in PROCESS_SKILLS:
                parts.append(PROCESS_SKILLS[name])
    return '\n\n'.join(parts)


class AgentState(TypedDict, total=False):
    messages: list[Any]
    events: list[dict[str, Any]]
    final: str
    approved: bool
    last_query: dict[str, Any] | None
    last_query_at: str | None
    skills: list[str]
    has_pending: bool
    active_agent: str  # Supervisor 领域路由：'bill'（账单）| 'schedule'（日程）| 'search'（搜索）| 'general'（通用兜底）
    tool_calls_done: int  # 工具调用次数（成本护栏，跨段/跨审批由 checkpoint 持久累计）


class SubGraphState(TypedDict, total=False):
    """领域子图私有状态（与主图 AgentState 分离）。

    langgraph 1.x 对子图按 channel 名自动做 input/output 映射：
    - 输入隔离：主图只有与本 schema 同名的字段才会传入子图，
      skills / has_pending / active_agent 等主图调度字段子图不可见、也不会被改动；
    - 输出隔离：子图只把本 schema 声明的字段写回主图，子图内部的
      私有中间结果（如 _bill_rows / _search_items，若未来需要）不会污染主图；
    - 子图运行完，主图自己的字段保持原值，checkpoint 恢复与追加任务不受影响。
    """
    messages: list[Any]
    events: list[dict[str, Any]]
    final: str
    approved: bool
    last_query: dict[str, Any] | None
    last_query_at: str | None
    tool_calls_done: int
    # —— 以下为子图私有中间结果（仅子图内部使用，不写回主图）——
    _bill_rows: list[dict[str, Any]] | None
    _search_items: list[dict[str, Any]] | None
    _schedule_events: list[dict[str, Any]] | None


def _event(type_, payload):
    return {'type': type_, 'payload': payload, 'at': now_iso()}


# 查询记忆有效期：超过该时长视为过期，不再注入「最近一次查询范围」防止跨场景误用
MEMO_TTL_SECONDS = 24 * 3600


def _query_memo_expired(last_query_at: str | None) -> bool:
    """记忆过期判断：无时间戳（旧状态/测试）视为有效；有时间戳且超过 TTL 视为过期。"""
    if not last_query_at:
        return False
    try:
        ts = datetime.fromisoformat(last_query_at)
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return (now - ts).total_seconds() > MEMO_TTL_SECONDS


def _query_memo_text(last_query, expired: bool = False):
    """把最近一次查询的落库参数转成记忆文本，注入模型上下文；过期时不注入。"""
    if last_query is None or expired:
        return None
    filters = last_query.get('filters') or {}
    parts = []
    if filters.get('start_date'):
        parts.append(f"从 {filters['start_date']}")
    if filters.get('end_date'):
        parts.append(f"到 {filters['end_date']}")
    if filters.get('category'):
        parts.append(f"分类={filters['category']}")
    if filters.get('transaction_type'):
        parts.append(f"交易类型={filters['transaction_type']}")
    if filters.get('merchant'):
        parts.append(f"商户={filters['merchant']}")
    if filters.get('direction'):
        parts.append(f"方向={filters['direction']}")
    if filters.get('min_amount') is not None:
        parts.append(f"金额≥{filters['min_amount']}")
    if filters.get('max_amount') is not None:
        parts.append(f"金额≤{filters['max_amount']}")
    scope = '、'.join(parts) if parts else '全部时间'
    count, total = last_query.get('count'), last_query.get('total')
    extra = f"，共 {count} 笔、合计 ¥{total}" if count is not None else ''
    return ('【当前对话记忆·最近一次账单查询】范围：' + scope + extra + '。'
            '用户之后说「只要…」「再筛…」「加上…」等补充条件时，默认在此范围内二次筛选；'
            '除非用户明确要求新的时间范围或重新查询，否则不要发起新的全量查询。')


def initial_input(run_id: str, message: str) -> dict[str, Any]:
    """首轮输入：先放纯铁律 system（intent 节点会按意图注入流程 Skill 并替换）。"""
    return {
        'messages': [SystemMessage(content=build_system_message([])), HumanMessage(content=message)],
        'events': [],
        'active_agent': 'general',
    }


# ---------- 上下文卫生：工具结果回喂模型前的治理 ----------

def sanitize_tool_content(content: str, max_chars: int | None = None,
                          max_lines: int | None = None) -> str:
    """工具结果文本卫生：行数 + 字符数双阈值裁剪（上下文卫生）。

    超长结果只把关键信息喂给模型，避免上下文爆炸稀释注意力；
    被裁剪时追加提示，引导模型用分页参数（limit/offset）继续取完整数据。
    """
    if not isinstance(content, str):
        return content
    if max_lines is not None:
        lines = content.split('\n')
        if len(lines) > max_lines:
            omitted = len(lines) - max_lines
            content = '\n'.join(lines[:max_lines]) + \
                f'\n…（已省略 {omitted} 行；如需完整数据可带 limit/offset 继续查询）'
    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars] + \
            '…（已截断；如需完整数据可带 limit/offset 继续查询）'
    return content


def _cache_key(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """缓存键：工具名 + 规范化参数（键排序，消除参数顺序差异）。"""
    try:
        norm = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        norm = str(args)
    return (name, norm)


class ToolResultCache:
    """进程内工具结果缓存（TTL 过期）：纯只读查询工具同参复用，省库往返与 token。

    上下文卫生的一环：模型在同一轮/多轮里反复查同一范围时，命中缓存不重跑数据库。
    """

    def __init__(self, ttl: float = 300):
        self._ttl = ttl
        self._store: dict[tuple[str, str], tuple[float, str, Any]] = {}

    def get(self, key: tuple[str, str]):
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, content, artifact = entry
        if self._ttl and time.monotonic() - ts > self._ttl:
            self._store.pop(key, None)
            return None
        return content, artifact

    def set(self, key: tuple[str, str], content: str, artifact: Any) -> None:
        self._store[key] = (time.monotonic(), content, artifact)

    def clear(self) -> None:
        self._store.clear()


# ---------- 节点工厂：主图与子图复用同一套节点实现 ----------

def make_agent_node(model, tools, *, memory_loader=None, model_retries=1, retry_backoff=0.5):
    """agent 节点工厂：模型调用 + 工具绑定 + 流式出口 + 失败重试 + 事件写入。
    主图（通用 agent）与账单子图（bill_agent）各自实例化，绑定各自的工具集与模型。"""

    async def agent(state, config=None):
        messages = list(state['messages'])
        events = list(state.get('events', []))
        # 工具调用次数硬护栏（成本护栏）：从运行时 config 读取，跨段/跨审批由 checkpoint
        # 的 tool_calls_done 累计。子图内循环不产生顶层事件推送，因此不能在 runner 层
        # 靠事件计数，必须在 agent 调用模型前基于 state 检查。
        max_tool_calls = None
        if config:
            max_tool_calls = config.get('configurable', {}).get('max_tool_calls')
        if max_tool_calls and state.get('tool_calls_done', 0) >= max_tool_calls:
            stop_text = (f'这次任务执行的工具调用次数超过了上限（{max_tool_calls} 次），'
                         '已安全停止；可把任务拆小或换个更简洁的说法重试。')
            messages.append(AIMessage(content=stop_text))
            return {'messages': messages, 'events': events, 'final': stop_text}
        # 记忆注入：最近一次查询的落库范围作为临时 SystemMessage 插在 System 之后，
        # 不写入 checkpoint 历史（每轮从 state.last_query 动态构造，避免历史重复膨胀）
        last_query = state.get('last_query')
        memo_text = _query_memo_text(last_query, _query_memo_expired(state.get('last_query_at')))
        memo = SystemMessage(content=memo_text) if memo_text else None
        model_messages = [messages[0], memo, *messages[1:]] if memo and messages else messages
        # 流式出口：chat.py 通过 config 注入，token 增量实时推给前端
        stream_sink = None
        if config:
            stream_sink = config.get('configurable', {}).get('stream_sink')

        async def call_model(msgs):
            bound = model.bind_tools(tools)
            chunks = []
            try:
                async for chunk in bound.astream(msgs):
                    # astream 产出 ChatGenerationChunk（含 .message=AIMessageChunk）
                    msg = getattr(chunk, 'message', chunk)
                    text = getattr(msg, 'content', None)
                    if stream_sink is not None and isinstance(text, str) and text:
                        await stream_sink(text)
                    chunks.append(msg)
            except (ValueError, NotImplementedError, AttributeError):
                # 模型不支持流式（如测试替身 GenericFakeChatModel 无 _astream）→ 回退普通调用
                if chunks:
                    raise
                response = await bound.ainvoke(msgs)
                text = getattr(response, 'content', None)
                if stream_sink is not None and isinstance(text, str) and text:
                    await stream_sink(text)
                return response
            if not chunks:
                return AIMessage(content='')
            acc = chunks[0]
            for c in chunks[1:]:
                acc = acc + c
            # 流式中断可能残留未解析成完整 tool_call 的 tool_call_chunks：
            # 丢弃残缺调用，避免其写入历史后污染后续模型上下文（OpenAI 协议会拒绝残缺 tool_call）
            if getattr(acc, 'tool_call_chunks', None) and not getattr(acc, 'tool_calls', None):
                acc = AIMessage(content=acc.content or '')
            return acc

        # 模型调用失败（超时/网络/限流）：指数退避重试 model_retries 次，不让整个 run 崩溃。
        # 失败提示不写入 checkpoint 历史（避免多轮累积污染后续上下文）。
        response = None
        last_exc = None
        for attempt in range(model_retries + 1):
            try:
                response = await call_model(model_messages)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < model_retries:
                    await asyncio.sleep(retry_backoff * (2 ** attempt))
        if response is None:
            return {'messages': messages, 'events': events,
                    'final': f'模型响应暂时不可用（{last_exc.__class__.__name__}），'
                             '请稍后重试或换个更简单的说法。'}
        messages.append(response)
        if getattr(response, 'tool_calls', None):
            for call in response.tool_calls:
                events.append(_event('intent_route', {
                    'tool': call['name'], 'args': call['args'],
                    'confidence': None, 'reason': 'llm_tool_call', 'source': 'llm',
                }))
                events.append(_event('tool_call_proposal', {
                    'tool': call['name'], 'args': call['args'],
                    'confidence': None, 'reason': 'llm_tool_call',
                }))
        update = {'messages': messages, 'events': events}
        if not getattr(response, 'tool_calls', None):
            update['final'] = response.content or ''
        return update

    return agent


def make_approve_node():
    """HITL 审批节点工厂：写工具经 interrupt 挂起，resume(approve/reject/edited_args) 后继续。
    主图与账单子图复用；子图内 interrupt 全局冒泡到顶层调用者（chat.py runner）。"""

    async def approve(state):
        last = state['messages'][-1]
        call = last.tool_calls[0]
        # tool_call id 由模型生成且保存在 checkpoint 中，恢复执行时稳定不变，
        # 因此用它派生的 approval_id 在 resume 后依然一致（避免每次重新生成导致不匹配）。
        approval_id = f'apr-{call["id"]}'
        payload = {'kind': 'tool_approval', 'approval_id': approval_id,
                   'tool': call['name'], 'args': call['args'],
                   'created_at': now_iso(),
                   'message': '这是一个写操作。请确认参数后再执行。'}
        decision = interrupt(payload)
        if not isinstance(decision, dict):
            decision = {'decision': decision}
        messages = list(state['messages'])
        events = list(state.get('events', []))
        if decision.get('approval_id') != approval_id:
            result = ToolError('APPROVAL_MISMATCH', '审批标识不匹配，请重新核对提案。',
                               stage='approval', retryable=False).envelope(tool=call['name'])
            events.append(_event('approval_error', {'result': result}))
            messages.append(SystemMessage(content='审批标识不匹配，写操作未执行。'))
            return {'messages': messages, 'events': events, 'approved': False}
        if decision.get('decision') == 'approve':
            if decision.get('edited_args') is not None:
                edited = decision['edited_args']
                new_call = {**call, 'args': edited}
                messages[-1] = AIMessage(content=last.content or '', tool_calls=[new_call])
            return {'approved': True, 'messages': messages}
        result = ToolError('USER_REJECTED', '用户拒绝了写操作',
                           stage='approval', retryable=False).envelope(tool=call['name'])
        events.append(_event('tool_result', {'tool': call['name'], 'result': result}))
        messages.append(SystemMessage(content=f'用户拒绝了 {call["name"]} 写操作，请向用户确认是否需要调整。'))
        return {'messages': messages, 'events': events, 'approved': False}

    return approve


def make_run_tools_node(tools, write_tools, *, tool_timeout=None, retry_backoff=0.5,
                        output_schemas=None, context_max_chars=None, context_max_lines=None,
                        tool_cache=None, cacheable_tools=None, tool_retries=0):
    """工具执行节点工厂：统一出口（幂等键 / 超时 / 重试 / 上下文卫生 / 输出契约 / 结构化错误）。
    主图（tools）与账单子图（bill_tools）各自实例化。"""
    tool_map = {t.name: t for t in tools}

    async def run_tools(state):
        last = state['messages'][-1]
        messages = list(state['messages'])
        events = list(state.get('events', []))
        done = state.get('tool_calls_done', 0)
        update = {'messages': messages, 'events': events}

        # HITL 不变量：见模块 docstring。路由只看 calls[0]，而这里原本遍历全部调用，
        # 「读 + 写」混合批会让写工具绕过 approve 直接执行。混合批一律只放行第一个调用，
        # 其余不执行、但必须补 ToolMessage（OpenAI 协议要求每个 tool_call 都有回执）。
        all_calls = list(last.tool_calls)
        if len(all_calls) > 1 and any(c['name'] in write_tools for c in all_calls):
            calls, dropped = all_calls[:1], all_calls[1:]
        else:
            calls, dropped = all_calls, []

        def emit_success(content, artifact_data, call_id, name, args, invocation_id,
                         cached_hit=False, attempts=1):
            """成功路径的统一出口：构造 ToolMessage、发事件、更新查询记忆。"""
            messages.append(ToolMessage(content=content, tool_call_id=call_id, name=name,
                                        artifact=artifact_data))
            events.append(_event('arguments_validated', {'tool': name, 'args': args}))
            events.append(_event('tool_result', {'tool': name, 'result': {
                'ok': True, 'tool': name,
                'invocation_id': None if cached_hit else invocation_id,
                'attempts': attempts, 'cached': cached_hit,
                'data': artifact_data, 'error': None,
            }}))
            # 查询上下文记忆：记录最近一次 query_bill 的落库范围，供后续补充条件注入
            if name == 'query_bill' and isinstance(artifact_data, dict):
                update['last_query'] = {
                    'filters': artifact_data.get('filters') or args,
                    'count': artifact_data.get('count'),
                    'total': artifact_data.get('total'),
                }
                update['last_query_at'] = now_iso()

        for call in calls:
            name, args, call_id = call['name'], call['args'], call['id']
            spec = tool_map[name]
            invocation_id = uuid4().hex
            cache_key = None
            cached = None
            if tool_cache is not None and cacheable_tools is not None and name in cacheable_tools:
                cache_key = _cache_key(name, args)
                cached = tool_cache.get(cache_key)
            if cached is not None:
                # 缓存命中：写入时已过卫生与输出契约校验，直接复用，不重跑库
                content, artifact_data = cached
                emit_success(content, artifact_data, call_id, name, args, invocation_id, cached_hit=True)
                continue
            # 写工具幂等键：与 approve 节点同源派生（approval_id = apr-{call id}，checkpoint 恢复后稳定）。
            # 键在【整个重试窗口】内保持（各 attempt 之间不 reset）：同一操作意图的所有执行
            # （含工具层自动重试）共用同一键，工具层按键查重/重放，防止「执行成功但响应超时后
            # 重试」造成重复副作用。写工具不缓存（有副作用），读工具不设键。
            idem_token = None
            if name in write_tools:
                idem_token = _IDEMPOTENCY_KEY.set(f'apr-{call_id}')
            # 工具层自动重试：只重试瞬时错误（超时 / 工具显式标记 retryable），指数退避；
            # 参数校验与确定性业务失败不重试（重试相同参数结果必然相同）。attempts 如实上报。
            attempts = 0
            last_err = None
            try:
                while True:
                    attempts += 1
                    try:
                        if tool_timeout:
                            async with asyncio.timeout(tool_timeout):
                                msg = await spec.arun(args, tool_call_id=call_id)
                        else:
                            msg = await spec.arun(args, tool_call_id=call_id)
                        break
                    except (TimeoutError, ToolError) as exc:
                        retryable = isinstance(exc, TimeoutError) or \
                            (isinstance(exc, ToolError) and exc.retryable)
                        if not retryable or attempts > tool_retries:
                            last_err = exc
                            break
                        # 瞬时错误：指数退避后重试（同一 call id → 幂等键不变，查重兜底）
                        await asyncio.sleep(retry_backoff * (2 ** (attempts - 1)))
                if last_err is not None:
                    raise last_err
                content = msg.content if hasattr(msg, 'content') else str(msg)
                artifact = getattr(msg, 'artifact', None)
                artifact_data = artifact.model_dump(mode='json') if hasattr(artifact, 'model_dump') else artifact
                # 上下文卫生：超长结果截断/超行保留前 N 行，防上下文爆炸
                content = sanitize_tool_content(content, context_max_chars, context_max_lines)
                # 输出契约校验：统一出口用声明的 output_schema 二次校验，
                # 防契约漂移/脏数据进入状态、前端与记忆；失败转 OUTPUT_INVALID 结构化错误。
                schema = output_schemas.get(name) if output_schemas else None
                if schema is not None and artifact_data is not None:
                    try:
                        artifact_data = schema.model_validate(artifact_data).model_dump(mode='json')
                    except ValidationError as exc:
                        result = {'ok': False, 'tool': name, 'invocation_id': invocation_id, 'attempts': attempts,
                                  'data': None,
                                  'error': {'code': 'OUTPUT_INVALID',
                                            'message': f'工具 {name} 返回结构不符合输出契约，结果未采用。',
                                            'stage': 'output', 'retryable': False,
                                            'details': {'validation': str(exc)}}}
                        messages.append(ToolMessage(content='工具返回结构不符合输出契约，结果未采用。',
                                                    tool_call_id=call_id, name=name, status='error'))
                        events.append(_event('tool_result', {'tool': name, 'result': result}))
                        continue
                if cache_key is not None:
                    tool_cache.set(cache_key, content, artifact_data)
                emit_success(content, artifact_data, call_id, name, args, invocation_id, attempts=attempts)
            except TimeoutError:
                # 工具执行超时（重试耗尽）：结构化错误回喂模型，标记 retryable 让模型可修正/换路径
                result = {'ok': False, 'tool': name, 'invocation_id': invocation_id, 'attempts': attempts,
                          'data': None,
                          'error': {'code': 'TOOL_TIMEOUT',
                                    'message': f'工具 {name} 执行超时（>{tool_timeout}s，重试 {tool_retries} 次后仍失败），已中止。',
                                    'stage': 'execution', 'retryable': True, 'details': None}}
                messages.append(ToolMessage(content='工具执行超时，已中止。', tool_call_id=call_id, name=name,
                                            status='error'))
                events.append(_event('tool_result', {'tool': name, 'result': result}))
            except ToolError as exc:
                result = exc.envelope(tool=name, invocation_id=invocation_id)
                result['attempts'] = attempts
                messages.append(ToolMessage(content=exc.message, tool_call_id=call_id, name=name,
                                            status='error'))
                events.append(_event('tool_result', {'tool': name, 'result': result}))
            except Exception as exc:
                if exc.__class__.__name__ == 'ValidationError':
                    code, stage, retryable = 'INVALID_ARGUMENTS', 'validation', True
                else:
                    code, stage, retryable = 'TOOL_FAILED', 'execution', False
                result = {'ok': False, 'tool': name, 'invocation_id': invocation_id, 'attempts': attempts,
                          'data': None,
                          'error': {'code': code, 'message': str(exc), 'stage': stage,
                                    'retryable': retryable, 'details': None}}
                messages.append(ToolMessage(content=str(exc), tool_call_id=call_id, name=name,
                                            status='error'))
                events.append(_event('tool_result', {'tool': name, 'result': result}))
            finally:
                if idem_token is not None:
                    _IDEMPOTENCY_KEY.reset(idem_token)
            done += 1  # 每个工具调用（无论成败）都计一次成本
        # 混合批被拦下的调用：不执行，但补结构化错误回执（同时满足协议配对与模型自纠正）
        for call in dropped:
            name, call_id = call['name'], call['id']
            result = {'ok': False, 'tool': name, 'invocation_id': uuid4().hex, 'attempts': 0,
                      'data': None,
                      'error': {'code': 'PARALLEL_WRITE_BLOCKED',
                                'message': f'写操作 {name} 不能与其他工具调用并行提交，本次未执行；'
                                           '请立刻单独重新发出这一个调用（不要带其他工具），会先经用户确认。',
                                'stage': 'policy', 'retryable': True, 'details': None}}
            messages.append(ToolMessage(
                content=result['error']['message'], tool_call_id=call_id, name=name, status='error'))
            events.append(_event('tool_result', {'tool': name, 'result': result}))
        update['tool_calls_done'] = done
        return update

    return run_tools


def _route_after_agent(state, write_tools):
    last = state['messages'][-1]
    if not getattr(last, 'tool_calls', None):
        # 无工具调用：先过输入检查点，看用户是否有追加输入（Codex 模式执行中追加）
        return 'check_input'
    return 'approve' if last.tool_calls[0]['name'] in write_tools else 'tools'


def _route_after_approve(state):
    return 'tools' if state.get('approved') else 'agent'


def make_bill_subgraph(model, tools, write_tools, *, memory_loader=None,
                       tool_timeout=None, model_retries=1, retry_backoff=0.5,
                       output_schemas=None, context_max_chars=None, context_max_lines=None,
                       tool_cache=None, cacheable_tools=None, tool_retries=0):
    """账单 Agent 子图（Supervisor 的一个领域 Worker）：
    bill_agent →（写）bill_approve /（读）bill_tools → 回 bill_agent 循环 → END 返回主图。
    - 不设输入检查点（interrupt 只在 Supervisor 层，减少中断次数）；
    - 写工具审批 interrupt 在子图内触发，全局冒泡到顶层调用者（chat.py runner），
      resume 后从子图断点继续；
    - 子图使用私有 SubGraphState：与主图 AgentState 按 channel 名自动映射，
      主图调度字段（skills/has_pending/active_agent）不进入子图，子图私有
      中间结果不写回主图，实现状态隔离。"""
    agent_node = make_agent_node(model, tools, memory_loader=memory_loader,
                                 model_retries=model_retries, retry_backoff=retry_backoff)
    approve_node = make_approve_node()
    tools_node = make_run_tools_node(tools, write_tools, tool_timeout=tool_timeout,
                                     retry_backoff=retry_backoff, output_schemas=output_schemas,
                                     context_max_chars=context_max_chars,
                                     context_max_lines=context_max_lines,
                                     tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                     tool_retries=tool_retries)

    def route_after_bill_agent(state):
        last = state['messages'][-1]
        if not getattr(last, 'tool_calls', None):
            return END  # 子图完成，回主图
        return 'bill_approve' if last.tool_calls[0]['name'] in write_tools else 'bill_tools'

    def route_after_bill_approve(state):
        # 子图节点命名空间：approved → bill_tools，否则回 bill_agent 重新决策
        return 'bill_tools' if state.get('approved') else 'bill_agent'

    sub = StateGraph(SubGraphState)
    sub.add_node('bill_agent', agent_node)
    sub.add_node('bill_approve', approve_node)
    sub.add_node('bill_tools', tools_node)
    sub.add_edge(START, 'bill_agent')
    sub.add_conditional_edges('bill_agent', route_after_bill_agent,
                              {'bill_tools': 'bill_tools', 'bill_approve': 'bill_approve', END: END})
    sub.add_conditional_edges('bill_approve', route_after_bill_approve,
                              {'bill_tools': 'bill_tools', 'bill_agent': 'bill_agent'})
    # 工具执行后直接回 bill_agent 继续消化结果（子图内不过输入检查点）
    sub.add_edge('bill_tools', 'bill_agent')
    return sub.compile()


def make_search_subgraph(model, tools, write_tools, *, memory_loader=None,
                         tool_timeout=None, model_retries=1, retry_backoff=0.5,
                         output_schemas=None, context_max_chars=None, context_max_lines=None,
                         tool_cache=None, cacheable_tools=None, tool_retries=0):
    """搜索 Agent 子图（Supervisor 的领域 Worker）：
    search_agent →（写）search_approve /（读）search_tools → 回 search_agent 循环 → END 返回主图。
    - 搜索主路径为只读（web_search 直接执行），但子图内若模型发起写操作（建卡等）
      同样经 approve 审批，保证 HITL 不因领域分流而旁路；
    - 不设输入检查点（interrupt 只在 Supervisor 层，减少中断次数）；
    - 子图使用私有 SubGraphState（状态隔离，见 make_bill_subgraph 说明）。"""
    agent_node = make_agent_node(model, tools, memory_loader=memory_loader,
                                 model_retries=model_retries, retry_backoff=retry_backoff)
    approve_node = make_approve_node()
    tools_node = make_run_tools_node(tools, write_tools, tool_timeout=tool_timeout,
                                     retry_backoff=retry_backoff, output_schemas=output_schemas,
                                     context_max_chars=context_max_chars,
                                     context_max_lines=context_max_lines,
                                     tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                     tool_retries=tool_retries)

    def route_after_search_agent(state):
        last = state['messages'][-1]
        if not getattr(last, 'tool_calls', None):
            return END  # 子图完成，回主图
        return 'search_approve' if last.tool_calls[0]['name'] in write_tools else 'search_tools'

    def route_after_search_approve(state):
        # 子图节点命名空间：approved → search_tools，否则回 search_agent 重新决策
        return 'search_tools' if state.get('approved') else 'search_agent'

    sub = StateGraph(SubGraphState)
    sub.add_node('search_agent', agent_node)
    sub.add_node('search_approve', approve_node)
    sub.add_node('search_tools', tools_node)
    sub.add_edge(START, 'search_agent')
    sub.add_conditional_edges('search_agent', route_after_search_agent,
                              {'search_tools': 'search_tools', 'search_approve': 'search_approve', END: END})
    sub.add_conditional_edges('search_approve', route_after_search_approve,
                              {'search_tools': 'search_tools', 'search_agent': 'search_agent'})
    sub.add_edge('search_tools', 'search_agent')
    return sub.compile()


def make_schedule_subgraph(model, tools, write_tools, *, memory_loader=None,
                           tool_timeout=None, model_retries=1, retry_backoff=0.5,
                           output_schemas=None, context_max_chars=None, context_max_lines=None,
                           tool_cache=None, cacheable_tools=None, tool_retries=0):
    """日程 Agent 子图（Supervisor 的领域 Worker）：
    schedule_agent →（写）schedule_approve /（读）schedule_tools → 回 schedule_agent 循环 → END 返回主图。
    - 读路径（list_events）直接执行；写路径（建/完成/改期）经 approve 审批，HITL 不因领域分流旁路；
    - 不设输入检查点（interrupt 只在 Supervisor 层，减少中断次数）；
    - 子图使用私有 SubGraphState（状态隔离，见 make_bill_subgraph 说明）。"""
    agent_node = make_agent_node(model, tools, memory_loader=memory_loader,
                                 model_retries=model_retries, retry_backoff=retry_backoff)
    approve_node = make_approve_node()
    tools_node = make_run_tools_node(tools, write_tools, tool_timeout=tool_timeout,
                                     retry_backoff=retry_backoff, output_schemas=output_schemas,
                                     context_max_chars=context_max_chars,
                                     context_max_lines=context_max_lines,
                                     tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                     tool_retries=tool_retries)

    def route_after_schedule_agent(state):
        last = state['messages'][-1]
        if not getattr(last, 'tool_calls', None):
            return END  # 子图完成，回主图
        return 'schedule_approve' if last.tool_calls[0]['name'] in write_tools else 'schedule_tools'

    def route_after_schedule_approve(state):
        # 子图节点命名空间：approved → schedule_tools，否则回 schedule_agent 重新决策
        return 'schedule_tools' if state.get('approved') else 'schedule_agent'

    sub = StateGraph(SubGraphState)
    sub.add_node('schedule_agent', agent_node)
    sub.add_node('schedule_approve', approve_node)
    sub.add_node('schedule_tools', tools_node)
    sub.add_edge(START, 'schedule_agent')
    sub.add_conditional_edges('schedule_agent', route_after_schedule_agent,
                              {'schedule_tools': 'schedule_tools',
                               'schedule_approve': 'schedule_approve', END: END})
    sub.add_conditional_edges('schedule_approve', route_after_schedule_approve,
                              {'schedule_tools': 'schedule_tools', 'schedule_agent': 'schedule_agent'})
    sub.add_edge('schedule_tools', 'schedule_agent')
    return sub.compile()


def make_graph(model, tools, write_tools, checkpointer=None, memory_loader=None,
               *, tool_timeout=None, model_retries=1, retry_backoff=0.5, output_schemas=None,
               context_max_chars=None, context_max_lines=None,
               tool_cache=None, cacheable_tools=None, tool_retries=0):
    """memory_loader: async callable → str | None，返回长期记忆文本（agent_memory 全量）。
    由调用方注入数据库会话（main.py 提供闭包），图内保持无副作用。
    运行时治理参数：
    - tool_timeout: float | None，单次工具执行超时秒数；None 表示不启用。
    - model_retries: 模型调用失败额外重试次数（指数退避 retry_backoff * 2^attempt）。
    - retry_backoff: 首次重试前的基础退避秒数。
    - tool_retries: 工具执行失败自动重试次数（指数退避 retry_backoff * 2^(attempt-1)；
      0=禁用）。只重试瞬时错误（超时 / 工具显式标记 retryable），参数校验与确定性
      业务失败不重试；重试复用同一 tool call id，幂等键贯穿整个重试窗口，写工具
      按键查重兜底，不会重复建卡。
    - output_schemas: dict[str, Pydantic模型] | None，工具输出契约。
      run_tools 统一出口对 artifact 二次校验，失败转 OUTPUT_INVALID 结构化错误。
    上下文卫生参数（None 表示不启用）：
    - context_max_chars: 单条工具结果文本字符上限，超长截断。
    - context_max_lines: 单条工具结果文本行数上限，超行保留前 N 行。
    - tool_cache: ToolResultCache | None，查询工具结果缓存实例。
    - cacheable_tools: frozenset[str] | None，可缓存工具名集合（纯只读）。
      命中缓存时跳过库执行，事件带 cached=True 标记。"""
    agent_node = make_agent_node(model, tools, memory_loader=memory_loader,
                                 model_retries=model_retries, retry_backoff=retry_backoff)
    approve_node = make_approve_node()
    tools_node = make_run_tools_node(tools, write_tools, tool_timeout=tool_timeout,
                                     retry_backoff=retry_backoff, output_schemas=output_schemas,
                                     context_max_chars=context_max_chars,
                                     context_max_lines=context_max_lines,
                                     tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                     tool_retries=tool_retries)
    bill_subgraph = make_bill_subgraph(model, tools, write_tools, memory_loader=memory_loader,
                                       tool_timeout=tool_timeout, model_retries=model_retries,
                                       retry_backoff=retry_backoff, output_schemas=output_schemas,
                                       context_max_chars=context_max_chars,
                                       context_max_lines=context_max_lines,
                                       tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                       tool_retries=tool_retries)
    search_subgraph = make_search_subgraph(model, tools, write_tools, memory_loader=memory_loader,
                                           tool_timeout=tool_timeout, model_retries=model_retries,
                                           retry_backoff=retry_backoff, output_schemas=output_schemas,
                                           context_max_chars=context_max_chars,
                                           context_max_lines=context_max_lines,
                                           tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                           tool_retries=tool_retries)
    schedule_subgraph = make_schedule_subgraph(model, tools, write_tools, memory_loader=memory_loader,
                                               tool_timeout=tool_timeout, model_retries=model_retries,
                                               retry_backoff=retry_backoff, output_schemas=output_schemas,
                                               context_max_chars=context_max_chars,
                                               context_max_lines=context_max_lines,
                                               tool_cache=tool_cache, cacheable_tools=cacheable_tools,
                                               tool_retries=tool_retries)

    async def intent(state):
        """Supervisor 意图路由：按最新用户消息命中关键词 → 注入对应流程 Skill 并重建 system；
        同时注入长期记忆（跨会话偏好/事实）。领域三路：账单域 → bill（账单子图）、
        搜索域 → search（搜索子图）、未命中 → general（通用 agent 兜底）。
        无事件产生（保持前端事件协议不变）；续轮/首轮都会重跑，system 始终贴合当前意图。"""
        messages = list(state['messages'])
        text = ''
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                text = str(m.content)
                break
        skills = detect_skills(text)
        memory_text = None
        if memory_loader is not None:
            try:
                memory_text = await memory_loader()
            except Exception:
                memory_text = None
        system = SystemMessage(content=build_system_message(skills, memory_text))
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = system
        else:
            messages.insert(0, system)
        return {'messages': messages, 'skills': skills,
                'active_agent': detect_agent(skills)}

    # 领域路由表：active_agent → 对应节点/子图（check_input 放行时回原领域）
    AGENT_ROUTE_MAP = {'bill': 'bill_subgraph', 'search': 'search_subgraph',
                       'schedule': 'schedule_subgraph', 'general': 'agent'}

    def route_after_intent(state):
        return AGENT_ROUTE_MAP.get(state.get('active_agent', 'general'), 'agent')

    async def check_input(state):
        """输入检查点（执行中可追加任务）：每轮之间挂起，等待 chat.py 的循环 runner
        告知是否有用户追加输入。有输入 → 追加 HumanMessage 并回 intent 重新路由
        （新指令可能切换领域，不重跑已完成步骤，checkpoint 保证历史在）；
        无输入 → 放行（有工具结果待处理回原领域，否则 END）。"""
        decision = interrupt({'kind': 'input_checkpoint'})
        if isinstance(decision, dict) and decision.get('new_input'):
            messages = list(state['messages'])
            messages.append(HumanMessage(content=decision['new_input']))
            return {'messages': messages, 'has_pending': True}
        return {'has_pending': False}

    def route_after_check(state):
        """有追加输入 → 回 intent 重新路由（可能换领域）；
        无输入且上一条是工具结果（还有下一轮）→ 回原领域（账单/搜索子图或通用 agent）；
        否则结束。"""
        if state.get('has_pending'):
            return 'intent'
        last = state['messages'][-1] if state.get('messages') else None
        if isinstance(last, ToolMessage):
            return AGENT_ROUTE_MAP.get(state.get('active_agent', 'general'), 'agent')
        return END

    def route_after_agent(state):
        return _route_after_agent(state, write_tools)

    graph = StateGraph(AgentState)
    graph.add_node('intent', intent)
    graph.add_node('agent', agent_node)
    graph.add_node('approve', approve_node)
    graph.add_node('tools', tools_node)
    graph.add_node('check_input', check_input)
    graph.add_node('bill_subgraph', bill_subgraph)
    graph.add_node('search_subgraph', search_subgraph)
    graph.add_node('schedule_subgraph', schedule_subgraph)
    graph.add_edge(START, 'intent')
    graph.add_conditional_edges('intent', route_after_intent,
                                {'agent': 'agent', 'bill_subgraph': 'bill_subgraph',
                                 'search_subgraph': 'search_subgraph',
                                 'schedule_subgraph': 'schedule_subgraph'})
    graph.add_edge('bill_subgraph', 'check_input')
    graph.add_edge('search_subgraph', 'check_input')
    graph.add_edge('schedule_subgraph', 'check_input')
    graph.add_conditional_edges('agent', route_after_agent,
                                {'tools': 'tools', 'approve': 'approve', 'check_input': 'check_input'})
    graph.add_conditional_edges('approve', _route_after_approve,
                                {'tools': 'tools', 'agent': 'agent'})
    # 通用域每轮工具执行后也过输入检查点：Codex 模式「轮与轮之间让出输入机会」
    graph.add_edge('tools', 'check_input')
    graph.add_conditional_edges('check_input', route_after_check,
                                {'agent': 'agent', 'bill_subgraph': 'bill_subgraph',
                                 'search_subgraph': 'search_subgraph',
                                 'schedule_subgraph': 'schedule_subgraph',
                                 'intent': 'intent', END: END})
    # 步数护栏（recursion_limit）由调用方在运行时通过 config 传入（langgraph 1.x 不支持 compile 传参），
    # 超限时抛 GraphRecursionError，由 chat.py 转成友好 final 而非崩溃。
    return graph.compile(checkpointer=checkpointer)
