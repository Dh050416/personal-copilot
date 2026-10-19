"""LangChain @tool 工具层（企业主流写法）+ 结构化输出 + 治理标记。

三个定制点的落点：
- 结构化 JSON：工具返回 `(summary_text, Pydantic模型)`，response_format='content_and_artifact'
  把「给模型的文本」和「给前端/状态的结构化数据」分离，模型读文本、界面读 artifact。
- schema 校验：args_schema 显式 Pydantic（StrictModel），调用前强校验；
  OUTPUT_SCHEMAS 声明每个工具的输出契约，run_tools 统一出口二次校验（防契约漂移/脏数据）。
- 治理标记：WRITE_TOOLS / TOOL_PERMISSIONS 声明写工具与所需权限，供图做 HITL 审批。
"""
import re
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from datetime import datetime, time
from html import unescape
from uuid import uuid4

import httpx
from langchain_core.tools import tool
from sqlalchemy import func, select

from app.data.models import AgentMemory, Bill, IdempotencyRecord, ScheduleEvent, TrackingCard, now_iso
from app.data.views import bill_view, card_view, event_view
from app.errors import ToolError
from app.schemas import (
    CardBatchView,
    CardList,
    CardView,
    CompleteEventArgs,
    CompleteTrackingCardArgs,
    CreateCardsFromQueryArgs,
    CreateEventArgs,
    CreateTrackingCardArgs,
    EventList,
    EventView,
    ListEventsArgs,
    ListTrackingCardsArgs,
    QueryBillArgs,
    QueryBillResult,
    SaveMemoryArgs,
    SaveMemoryResult,
    UpdateEventArgs,
    WebSearchArgs,
    WebSearchItem,
    WebSearchResult,
)
from app.services.card_service import complete_card, create_card
from app.services.schedule_service import complete_event as service_complete_event
from app.services.schedule_service import create_event as service_create_event
from app.services.schedule_service import update_event as service_update_event

# 写工具集合：图遇到这些工具必须先走 interrupt 人工审批
WRITE_TOOLS = frozenset({'create_tracking_card', 'complete_tracking_card', 'create_cards_from_query',
                         'create_event', 'complete_event', 'update_event'})
# 可缓存工具：纯只读查询，结果在 TTL 内可复用（上下文卫生：同参重复查询不重跑库）
CACHEABLE_TOOLS = frozenset({'query_bill', 'list_tracking_cards', 'web_search', 'list_events'})
# 写工具所需权限（声明式，供网关/审计使用）
TOOL_PERMISSIONS = {
    'create_tracking_card': ('billing:card:create',),
    'complete_tracking_card': ('billing:card:complete',),
    'create_cards_from_query': ('billing:card:create',),
    'create_event': ('schedule:event:create',),
    'complete_event': ('schedule:event:complete',),
    'update_event': ('schedule:event:update',),
}

# 输出契约：工具名 → 输出 schema。run_tools 在统一出口对 artifact 二次校验，
# 失败转 OUTPUT_INVALID 结构化错误（结果不进状态/前端/记忆）。未声明的工具不校验。
OUTPUT_SCHEMAS = {
    'query_bill': QueryBillResult,
    'create_tracking_card': CardView,
    'create_cards_from_query': CardBatchView,
    'complete_tracking_card': CardView,
    'save_memory': SaveMemoryResult,
    'list_tracking_cards': CardList,
    'web_search': WebSearchResult,
    'list_events': EventList,
    'create_event': EventView,
    'complete_event': EventView,
    'update_event': EventView,
}

# 写工具幂等键注入通道：run_tools 在写工具分支设置（= approval_id，由 tool_call id 派生、
# checkpoint 稳定），工具内读取并传给服务层做「查重 → 重放/新建」。用 ContextVar 而非参数传递：
# 1) args_schema 均为 extra='forbid'，模型契约不能携带内部字段；
# 2) StructuredTool.arun 会过滤多余 kwargs，额外参数传不进工具函数。
# asyncio 单事件循环下由 Task 隔离，未来读工具并行执行也不互相污染。
_IDEMPOTENCY_KEY: ContextVar[str | None] = ContextVar('idempotency_key', default=None)


def build_tools(db):
    """闭包注入数据库会话，返回 LangChain 工具列表。"""

    @tool('query_bill', args_schema=QueryBillArgs, response_format='content_and_artifact')
    async def query_bill(**kwargs):
        """查询历史账单，按日期、消费分类、商户/转账对方、金额、支付方式、交易状态及交易类型筛选。

        转账记录用 transaction_type=转账；给某人用 merchant 及 direction=expense，收到某人用 income，
        往来双方用 direction=null。不是发起转账。total 为所有匹配记录金额合计，非净支出，明细最多 100 条；
        结果较多时分页：用 offset 跳过已看部分、limit 控制每页条数。
        """
        args = QueryBillArgs.model_validate(kwargs)
        filters = []
        if args.start_date:
            filters.append(Bill.tx_time >= datetime.combine(args.start_date, time.min).isoformat(timespec='seconds'))
        if args.end_date:
            filters.append(Bill.tx_time <= datetime.combine(args.end_date, time.max).isoformat(timespec='seconds'))
        if args.category:
            filters.append(Bill.category == args.category)
        if args.merchant:
            filters.append(Bill.merchant == args.merchant if args.merchant_match == 'exact'
                           else Bill.merchant.contains(args.merchant, autoescape=True))
        if args.transaction_type:
            filters.append(Bill.transaction_type == args.transaction_type)
        if args.payment_method:
            filters.append(Bill.payment_method.contains(args.payment_method, autoescape=True))
        if args.transaction_status:
            filters.append(Bill.transaction_status.contains(args.transaction_status, autoescape=True))
        if args.min_amount is not None:
            cents = int(args.min_amount * 100)
            filters.append(Bill.amount_cents >= cents if args.min_amount_inclusive else Bill.amount_cents > cents)
        if args.max_amount is not None:
            cents = int(args.max_amount * 100)
            filters.append(Bill.amount_cents <= cents if args.max_amount_inclusive else Bill.amount_cents < cents)
        if args.direction and args.direction != 'neutral':
            filters.append(Bill.direction == args.direction)
        async with db.sessions() as session:
            count, total = (await session.execute(
                select(func.count(Bill.id), func.coalesce(func.sum(Bill.amount_cents), 0)).where(*filters)
            )).one()
            bills = list(await session.scalars(
                select(Bill).where(*filters).order_by(Bill.tx_time.desc(), Bill.id.desc())
                .offset(args.offset).limit(args.limit)))
        warnings = []
        if args.category:
            warnings.append('消费分类含自动推断，未分类记录归为其他；分类筛选可能遗漏未正确分类的记录。')
        async with db.sessions() as session:
            for key, label in (('payment_method', '支付方式'), ('transaction_status', '交易状态')):
                if getattr(args, key) and await session.scalar(
                        select(func.count(Bill.id)).where(getattr(Bill, key) == '未知')):
                    warnings.append(f'库中部分旧记录缺少{label}，请重新导入原始账单补齐；本次筛选不包含缺失记录。')
        total_text = format(total / 100, '.2f')
        result = QueryBillResult(
            count=count, total=total_text, returned_count=len(bills), truncated=count > len(bills),
            filters=args.model_dump(mode='json'), items=[bill_view(b) for b in bills], warnings=warnings,
        )
        summary = (f'查询范围：{scope_text(args)}，共 {count} 笔，记录金额合计 {total_text} 元，'
                   f'返回第 {args.offset + 1}-{args.offset + len(bills)} 条明细（{len(bills)} 条）；'
                   f'按原始账单金额统计，未做退款抵扣。')
        if bills:
            summary += '\n明细（如需建卡关联，用 ID 字段）：'
            for b in bills[:10]:
                summary += (f'\n- ID {b.id}｜{b.tx_time}｜{b.merchant}｜¥{format(b.amount_cents / 100, ".2f")}'
                            f'｜{b.transaction_type}｜{b.category}')
        if count > args.offset + len(bills):
            summary += (f'\n还有 {count - args.offset - len(bills)} 条未显示，'
                        f'如需继续请用 offset={args.offset + len(bills)} 查询下一页。')
        if warnings:
            summary += '\n提示：' + '；'.join(warnings)
        return summary, result

    @tool('create_tracking_card', args_schema=CreateTrackingCardArgs, response_format='content_and_artifact')
    async def create_tracking_card(**kwargs):
        """创建待办卡片，必须经用户确认。关联账单 ID 必须来自查询。

        交易属性（方向/类型/对方/金额/时间）优先用调用参数；未指定时若关联账单，
        自动从该账单快照复制（对方=商户/转账对方、时间=交易时间、金额=账单金额、方向=账单方向）。
        """
        args = CreateTrackingCardArgs.model_validate(kwargs)
        # 幂等：键 = 框架注入的 approval_id（同一次确认过的操作意图），服务层查重/重放，不重复建卡
        view = await create_card(db, args, _IDEMPOTENCY_KEY.get())
        summary = f'已创建{view["category"]}·{view["purpose"]}待办（卡片 ID：{view["id"]}）'
        if view['amount'] is not None:
            sign = '-' if view['direction'] == 'expense' else '+'
            summary += f'；{sign}¥{view["amount"]}'
        if view['counterparty']:
            role = '收款人' if view['direction'] == 'expense' else '付款人'
            summary += f'；{role}：{view["counterparty"]}'
        if view['occurred_at']:
            summary += f'；时间：{view["occurred_at"]}'
        summary += '。'
        return summary, CardView(**view)

    @tool('create_cards_from_query', args_schema=CreateCardsFromQueryArgs,
          response_format='content_and_artifact')
    async def create_cards_from_query(**kwargs):
        """按查询范围批量建卡（单事务）：为所有匹配记录一次建成卡片，必须经用户确认。

        交易属性（方向/对方/金额/时间）自动从每笔账单快照复制；
        全部同分类用 card_category；各张分类不同用 category_rules
        （按对方/类型/方向匹配，顺序命中第一条，未命中回退 card_category）。
        limit 限制最多建卡笔数，防止误操作超量。
        """
        args = CreateCardsFromQueryArgs.model_validate(kwargs)
        # 幂等键 = 框架注入的 approval_id。命中记录 → 重放首次结果；未命中 → 同事务建卡+写记录，
        # 保证「执行成功但响应超时后重试」不会二次建卡（批次无法共用单卡 invocation_id，用记录表存证）。
        key = _IDEMPOTENCY_KEY.get()
        filters = []
        if args.start_date:
            filters.append(Bill.tx_time >= datetime.combine(args.start_date, time.min).isoformat(timespec='seconds'))
        if args.end_date:
            filters.append(Bill.tx_time <= datetime.combine(args.end_date, time.max).isoformat(timespec='seconds'))
        if args.merchant:
            filters.append(Bill.merchant.contains(args.merchant, autoescape=True))
        if args.transaction_type:
            filters.append(Bill.transaction_type == args.transaction_type)
        if args.direction:
            filters.append(Bill.direction == args.direction)
        if args.min_amount is not None:
            filters.append(Bill.amount_cents >= int(args.min_amount * 100))
        if args.max_amount is not None:
            filters.append(Bill.amount_cents <= int(args.max_amount * 100))

        def classify(b):
            """分类优先级：use_bill_category（跟随账单分类）> category_rules 顺序命中 > card_category 兜底。"""
            if args.use_bill_category:
                return b.category or '其他'
            if args.category_rules:
                for rule in args.category_rules:
                    if rule.merchant and rule.merchant not in (b.merchant or ''):
                        continue
                    if rule.transaction_type and rule.transaction_type != b.transaction_type:
                        continue
                    if rule.direction and rule.direction != b.direction:
                        continue
                    return rule.category
            return args.card_category or '其他'

        cards = []
        total_cents = 0
        async with db.sessions() as session:
            # 单事务：查重 → 查询 → 批量建卡 → 幂等记录 一起提交/回滚
            # （查重在事务内，避免会话先 autobegin 再显式 begin() 冲突）
            async with session.begin():
                if key is not None:
                    rec = await session.get(IdempotencyRecord, key)
                    if rec is not None:
                        # 重放首次响应：内容与 artifact 与首次执行完全一致
                        return rec.content, CardBatchView.model_validate_json(rec.response_json)
                bills = list(await session.scalars(
                    select(Bill).where(*filters).order_by(Bill.tx_time.asc(), Bill.id.asc()).limit(args.limit)))
                for b in bills:
                    amount_cents = b.amount_cents
                    if amount_cents is not None:
                        total_cents += amount_cents
                    cards.append(TrackingCard(
                        bill_id=b.id, category=classify(b), purpose=args.purpose,
                        note=args.note, direction=b.direction or 'expense',
                        transaction_type=b.transaction_type or '其他',
                        counterparty=b.merchant or '', amount_cents=amount_cents,
                        occurred_at=b.tx_time or '', invocation_id=uuid4().hex,
                    ))
                views = []
                if cards:
                    session.add_all(cards)
                    await session.flush()
                    views = [card_view(c) for c in cards]
                total_text = format(total_cents / 100, '.2f')
                batch = None
                if views:
                    id_range = f'（卡片 ID：{views[0]["id"]}-{views[-1]["id"]}）' if len(views) > 1 \
                        else f'（卡片 ID：{views[0]["id"]}）'
                    cats = {}
                    for v in views:
                        cats[v['category']] = cats.get(v['category'], 0) + 1
                    summary = (f'已按查询范围批量创建 {len(views)} 张{args.purpose}待办'
                               f'{id_range}；合计 ¥{total_text}。分类分布：'
                               + '、'.join(f'{k} {v}张' for k, v in cats.items()) + '。')
                    batch = CardBatchView(total=len(views), card_ids=[v['id'] for v in views],
                                          total_amount=total_text, items=views)
                    if key is not None:
                        session.add(IdempotencyRecord(key=key, content=summary,
                                                      response_json=batch.model_dump_json()))
        if not views:
            return '查询范围内没有匹配的账单，未创建卡片。', CardBatchView(
                total=0, card_ids=[], total_amount='0.00', items=[])
        return summary, batch

    @tool('complete_tracking_card', args_schema=CompleteTrackingCardArgs,
          response_format='content_and_artifact')
    async def complete_tracking_card(**kwargs):
        """将激活的待办卡片标记为已完成（移入看板「已完成」区），必须经用户确认。

        卡片 ID 必须来自 list_tracking_cards 查询结果；幂等：已完成的卡片重复操作直接返回成功。
        """
        args = CompleteTrackingCardArgs.model_validate(kwargs)
        view = await complete_card(db, args.card_id)
        summary = f'已将{view["category"]}·{view["purpose"]}待办（卡片 ID：{view["id"]}）标记为已完成。'
        return summary, CardView(**view)

    @tool('save_memory', args_schema=SaveMemoryArgs, response_format='content_and_artifact')
    async def save_memory(**kwargs):
        """保存长期记忆：记录用户明确表达的偏好或事实（如「以后报销默认其他」「章鱼大王是常去的餐饮店」）。

        只记录用户当面明确表达的内容，不得推测、归纳或编造；
        key 用简短可读的中文短语；重复保存同一 key 会覆盖更新。
        """
        args = SaveMemoryArgs.model_validate(kwargs)
        async with db.sessions() as session:
            async with session.begin():
                existing = await session.get(AgentMemory, args.key)
                if existing is not None:
                    existing.value = args.value
                    existing.updated_at = now_iso()
                else:
                    session.add(AgentMemory(key=args.key, value=args.value, updated_at=now_iso()))
        return f'已记住：{args.key} = {args.value}', SaveMemoryResult(key=args.key, value=args.value)

    @tool('list_tracking_cards', args_schema=ListTrackingCardsArgs,
          response_format='content_and_artifact')
    async def list_tracking_cards(**kwargs):
        """读取事务卡片列表（分页，按 id 倒序）：limit 控制每页条数、offset 翻页。

        卡片较多时只读本页摘要即可；需要更多用 offset 继续查询，避免一次带回全量。
        """
        args = ListTrackingCardsArgs.model_validate(kwargs)
        async with db.sessions() as session:
            cards = await session.scalars(
                select(TrackingCard).order_by(TrackingCard.id.desc()).offset(args.offset).limit(args.limit))
            views = [card_view(card) for card in cards]
            total = await session.scalar(select(func.count(TrackingCard.id)))
        if not views:
            return '目前没有事务卡片。', CardList(items=[])
        summary = f'最近的事务卡片（本页 {len(views)} 张）：\n'
        summary += '\n'.join(f'#{v["id"]} {v["category"]}·{v["purpose"]} [{v["status"]}] {v["note"]}'
                             for v in views)
        if total and args.offset + len(views) < total:
            summary += (f'\n还有 {total - args.offset - len(views)} 张未显示，'
                        f'如需继续请用 offset={args.offset + len(views)} 查询下一页。')
        return summary, CardList(items=[CardView(**v) for v in views])

    @tool('web_search', args_schema=WebSearchArgs, response_format='content_and_artifact')
    async def web_search(**kwargs):
        """搜索互联网获取实时信息（新闻、攻略、天气、价格、评测、路线等本地账单里没有的外部资料）。

        适合旅游规划、最新消息、产品对比、路线攻略等需要外部信息的查询；
        账单/收支类查询不要用本工具，用 query_bill。结果来自公开网页，注意时效与来源可靠性。
        """
        args = WebSearchArgs.model_validate(kwargs)
        try:
            async with httpx.AsyncClient(
                    timeout=8, follow_redirects=True,
                    headers={'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36')}
            ) as client:
                resp = await client.get('https://cn.bing.com/search',
                                        params={'q': args.query, 'format': 'rss',
                                                'count': args.max_results})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError('SEARCH_UNAVAILABLE',
                            f'搜索服务暂时不可用（{exc.__class__.__name__}），请稍后重试。',
                            stage='execution', retryable=True)
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            raise ToolError('SEARCH_UNAVAILABLE', '搜索服务返回内容无法解析，请稍后重试。',
                            stage='execution', retryable=True)
        items = []
        for it in root.findall('.//item'):
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            desc = re.sub(r'<[^>]+>', '', it.findtext('description') or '')
            desc = unescape(desc).strip()
            if title:
                items.append(WebSearchItem(title=title, url=link, snippet=desc))
            if len(items) >= args.max_results:
                break
        if not items:
            raise ToolError('SEARCH_EMPTY',
                            f'没有找到与「{args.query}」相关的搜索结果，可换关键词重试。',
                            stage='execution', retryable=True)
        result = WebSearchResult(query=args.query, count=len(items), provider='bing', items=items)
        summary = f'搜索「{args.query}」共 {len(items)} 条结果（来源：必应）：'
        for it in items:
            summary += f'\n- {it.title}｜{it.url}'
            if it.snippet:
                summary += f'｜{it.snippet[:120]}'
        return summary, result

    @tool('list_events', args_schema=ListEventsArgs, response_format='content_and_artifact')
    async def list_events(**kwargs):
        """查询日程，按日期范围与状态筛选（分页，按开始时间倒序）。

        默认只查未完成（active）；查全部（含已完成）用 status=all，只看已完成用 status=done。
        结果较多时用 offset/limit 翻页。
        """
        args = ListEventsArgs.model_validate(kwargs)
        filters = []
        if args.start_date:
            filters.append(ScheduleEvent.start_at >= args.start_date.isoformat())
        if args.end_date:
            filters.append(ScheduleEvent.start_at <= args.end_date.isoformat() + 'T23:59:59')
        if args.status != 'all':
            filters.append(ScheduleEvent.status == args.status)
        async with db.sessions() as session:
            events = list(await session.scalars(
                select(ScheduleEvent).where(*filters)
                .order_by(ScheduleEvent.start_at.desc(), ScheduleEvent.id.desc())
                .offset(args.offset).limit(args.limit)))
            total = await session.scalar(select(func.count(ScheduleEvent.id)).where(*filters))
        views = [event_view(e) for e in events]
        if not views:
            return '目前没有匹配的日程。', EventList(items=[])
        summary = f'日程（本页 {len(views)} 条）：\n'
        for v in views:
            time_text = v['start_at'] + (f' 至 {v["end_at"]}' if v['end_at'] else '')
            loc = f'｜{v["location"]}' if v['location'] else ''
            note = f'｜{v["note"]}' if v['note'] else ''
            summary += f'\n#{v["id"]} {time_text} {v["title"]}{loc}{note}'
        if total and args.offset + len(views) < total:
            summary += (f'\n还有 {total - args.offset - len(views)} 条未显示，'
                        f'如需继续请用 offset={args.offset + len(views)} 查询下一页。')
        return summary, EventList(items=[EventView(**v) for v in views])

    @tool('create_event', args_schema=CreateEventArgs, response_format='content_and_artifact')
    async def create_event(**kwargs):
        """新建日程，必须经用户确认。时间支持 YYYY-MM-DD（全天）或 YYYY-MM-DDTHH:MM（精确到分）。"""
        args = CreateEventArgs.model_validate(kwargs)
        view = await service_create_event(db, args, _IDEMPOTENCY_KEY.get())
        summary = f'已创建日程 #{view["id"]}「{view["title"]}」'
        summary += f'；{view["start_at"]}'
        if view['end_at']:
            summary += f' 至 {view["end_at"]}'
        if view['location']:
            summary += f'；地点：{view["location"]}'
        summary += '。'
        return summary, EventView(**view)

    @tool('complete_event', args_schema=CompleteEventArgs, response_format='content_and_artifact')
    async def complete_event(**kwargs):
        """标记日程为已完成，必须经用户确认。日程 ID 必须来自查询结果。"""
        args = CompleteEventArgs.model_validate(kwargs)
        view = await service_complete_event(db, args.event_id)
        return f'已标记日程 #{view["id"]}「{view["title"]}」为完成。', EventView(**view)

    @tool('update_event', args_schema=UpdateEventArgs, response_format='content_and_artifact')
    async def update_event(**kwargs):
        """修改日程（改期/改标题/改地点/改备注），必须经用户确认。日程 ID 必须来自查询结果。"""
        args = UpdateEventArgs.model_validate(kwargs)
        view = await service_update_event(db, args)
        summary = f'已更新日程 #{view["id"]}「{view["title"]}」'
        summary += f'；{view["start_at"]}'
        if view['end_at']:
            summary += f' 至 {view["end_at"]}'
        if view['location']:
            summary += f'；地点：{view["location"]}'
        summary += '。'
        return summary, EventView(**view)

    return [query_bill, create_tracking_card, create_cards_from_query,
            complete_tracking_card, save_memory, list_tracking_cards, web_search,
            list_events, create_event, complete_event, update_event]


def scope_text(args):
    if args.start_date and args.end_date and args.start_date == args.end_date:
        return str(args.start_date)
    if args.start_date and args.end_date:
        return f'{args.start_date} 至 {args.end_date}'
    if args.start_date:
        return f'{args.start_date} 起'
    if args.end_date:
        return f'截至 {args.end_date}'
    return '全部时间'
