"""cards tool adapters; business persistence lives in services."""

from datetime import datetime, time
from uuid import uuid4

from langchain_core.tools import tool
from sqlalchemy import func, select

from app.data.models import Bill, IdempotencyRecord, TrackingCard
from app.data.views import card_view
from app.schemas import (
    CardBatchView,
    CardList,
    CardView,
    CompleteTrackingCardArgs,
    CreateCardsFromQueryArgs,
    CreateTrackingCardArgs,
    ListTrackingCardsArgs,
)
from app.services.card_service import complete_card, create_card
from app.tools.context import _IDEMPOTENCY_KEY, current_user_id


def build_cards_tools(db, embedding_client=None):
    @tool("create_tracking_card", args_schema=CreateTrackingCardArgs, response_format="content_and_artifact")
    async def create_tracking_card(**kwargs):
        """创建待办卡片，必须经用户确认。关联账单 ID 必须来自查询。

        交易属性（方向/类型/对方/金额/时间）优先用调用参数；未指定时若关联账单，
        自动从该账单快照复制（对方=商户/转账对方、时间=交易时间、金额=账单金额、方向=账单方向）。
        """
        args = CreateTrackingCardArgs.model_validate(kwargs)
        # 幂等：键 = 框架注入的 approval_id（同一次确认过的操作意图），服务层查重/重放，不重复建卡
        view = await create_card(db, current_user_id(), args, _IDEMPOTENCY_KEY.get())
        summary = f"已创建{view['category']}·{view['purpose']}待办（卡片 ID：{view['id']}）"
        if view["amount"] is not None:
            sign = "-" if view["direction"] == "expense" else "+"
            summary += f"；{sign}¥{view['amount']}"
        if view["counterparty"]:
            role = "收款人" if view["direction"] == "expense" else "付款人"
            summary += f"；{role}：{view['counterparty']}"
        if view["occurred_at"]:
            summary += f"；时间：{view['occurred_at']}"
        summary += "。"
        return summary, CardView(**view)

    @tool(
        "create_cards_from_query",
        args_schema=CreateCardsFromQueryArgs,
        response_format="content_and_artifact",
    )
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
        user_id = current_user_id()
        filters = [Bill.user_id == user_id]
        if args.start_date:
            filters.append(
                Bill.tx_time >= datetime.combine(args.start_date, time.min).isoformat(timespec="seconds")
            )
        if args.end_date:
            filters.append(
                Bill.tx_time <= datetime.combine(args.end_date, time.max).isoformat(timespec="seconds")
            )
        if args.merchant:
            filters.append(Bill.merchant.contains(args.merchant, autoescape=True))
        if args.transaction_type:
            filters.append(Bill.transaction_type == args.transaction_type)
        if args.category:
            # 消费分类筛选：之前批量工具漏了这个 filter，导致「把餐饮做成卡」会把全部分类都建出来
            filters.append(Bill.category == args.category)
        if args.direction:
            filters.append(Bill.direction == args.direction)
        # 金额边界：inclusive=True 表示含边界（>= / <=）；用户说「超过 N」必须传 inclusive=False
        # （之前 min_amount 恒定按 >= 处理，「超过 100」会把正好 100 元的账单也建进卡）
        if args.min_amount is not None:
            bound = int(args.min_amount * 100)
            filters.append(
                Bill.amount_cents >= bound if args.min_amount_inclusive else Bill.amount_cents > bound
            )
        if args.max_amount is not None:
            bound = int(args.max_amount * 100)
            filters.append(
                Bill.amount_cents <= bound if args.max_amount_inclusive else Bill.amount_cents < bound
            )

        def classify(b):
            """分类优先级：use_bill_category（跟随账单分类）> category_rules 顺序命中 > card_category 兜底。"""
            if args.use_bill_category:
                return b.category or "其他"
            if args.category_rules:
                for rule in args.category_rules:
                    if rule.merchant and rule.merchant not in (b.merchant or ""):
                        continue
                    if rule.transaction_type and rule.transaction_type != b.transaction_type:
                        continue
                    if rule.direction and rule.direction != b.direction:
                        continue
                    return rule.category
            return args.card_category or "其他"

        cards = []
        total_cents = 0
        async with db.sessions() as session:
            # 单事务：查重 → 查询 → 批量建卡 → 幂等记录 一起提交/回滚
            # （查重在事务内，避免会话先 autobegin 再显式 begin() 冲突）
            async with session.begin():
                if key is not None:
                    rec = await session.get(IdempotencyRecord, (user_id, key))
                    if rec is not None:
                        # 重放首次响应：内容与 artifact 与首次执行完全一致
                        return rec.content, CardBatchView.model_validate_json(rec.response_json)
                bills = list(
                    await session.scalars(
                        select(Bill)
                        .where(*filters)
                        .order_by(Bill.tx_time.asc(), Bill.id.asc())
                        .limit(args.limit)
                    )
                )
                # 按账单去重：这批账单里已经建过卡的（active/done 都算）直接跳过。
                # 没有这一步时，同一句话跑第二遍会把整批账单再建一遍（曾出现同一 bill 挂 3 张卡）。
                skipped = 0
                existing_bill_ids = set()
                if bills:
                    existing_bill_ids = set(
                        await session.scalars(
                            select(TrackingCard.bill_id).where(
                                TrackingCard.user_id == user_id,
                                TrackingCard.bill_id.in_([b.id for b in bills]),
                            )
                        )
                    )
                for b in bills:
                    if b.id in existing_bill_ids:
                        skipped += 1
                        continue
                    amount_cents = b.amount_cents
                    if amount_cents is not None:
                        total_cents += amount_cents
                    cards.append(
                        TrackingCard(
                            user_id=user_id,
                            bill_id=b.id,
                            category=classify(b),
                            purpose=args.purpose,
                            note=args.note,
                            direction=b.direction or "expense",
                            transaction_type=b.transaction_type or "其他",
                            counterparty=b.merchant or "",
                            amount_cents=amount_cents,
                            occurred_at=b.tx_time or "",
                            # 用户显式指定的看板栏目（lane）必须原样落库；漏传会导致
                            # 前端 laneFor() 退回 purpose=报销 → 全部堆进「未报销」栏。
                            lane=args.lane,
                            invocation_id=uuid4().hex,
                        )
                    )
                views = []
                if cards:
                    session.add_all(cards)
                    await session.flush()
                    views = [card_view(c) for c in cards]
                total_text = format(total_cents / 100, ".2f")
                batch = None
                if views:
                    id_range = (
                        f"（卡片 ID：{views[0]['id']}-{views[-1]['id']}）"
                        if len(views) > 1
                        else f"（卡片 ID：{views[0]['id']}）"
                    )
                    cats = {}
                    for v in views:
                        cats[v["category"]] = cats.get(v["category"], 0) + 1
                    summary = (
                        f"已按查询范围批量创建 {len(views)} 张{args.purpose}待办"
                        f"{id_range}；合计 ¥{total_text}。分类分布："
                        + "、".join(f"{k} {v}张" for k, v in cats.items())
                        + "。"
                    )
                    batch = CardBatchView(
                        total=len(views),
                        card_ids=[v["id"] for v in views],
                        total_amount=total_text,
                        items=views,
                    )
                    if key is not None:
                        session.add(
                            IdempotencyRecord(
                                user_id=user_id,
                                key=key,
                                content=summary,
                                response_json=batch.model_dump_json(),
                            )
                        )
        if not views:
            return "查询范围内没有匹配的账单，未创建卡片。", CardBatchView(
                total=0, card_ids=[], total_amount="0.00", items=[]
            )
        return summary, batch

    @tool(
        "complete_tracking_card", args_schema=CompleteTrackingCardArgs, response_format="content_and_artifact"
    )
    async def complete_tracking_card(**kwargs):
        """将激活的待办卡片标记为已完成（移入看板「已完成」区），必须经用户确认。

        卡片 ID 必须来自 list_tracking_cards 查询结果；幂等：已完成的卡片重复操作直接返回成功。
        """
        args = CompleteTrackingCardArgs.model_validate(kwargs)
        view = await complete_card(db, current_user_id(), args.card_id)
        summary = f"已将{view['category']}·{view['purpose']}待办（卡片 ID：{view['id']}）标记为已完成。"
        return summary, CardView(**view)

    @tool("list_tracking_cards", args_schema=ListTrackingCardsArgs, response_format="content_and_artifact")
    async def list_tracking_cards(**kwargs):
        """读取事务卡片列表（分页，按 id 倒序）：limit 控制每页条数、offset 翻页。

        卡片较多时只读本页摘要即可；需要更多用 offset 继续查询，避免一次带回全量。
        """
        args = ListTrackingCardsArgs.model_validate(kwargs)
        user_id = current_user_id()
        async with db.sessions() as session:
            cards = await session.scalars(
                select(TrackingCard)
                .where(TrackingCard.user_id == user_id)
                .order_by(TrackingCard.id.desc())
                .offset(args.offset)
                .limit(args.limit)
            )
            views = [card_view(card) for card in cards]
            total = await session.scalar(
                select(func.count(TrackingCard.id)).where(TrackingCard.user_id == user_id)
            )
        if not views:
            return "目前没有事务卡片。", CardList(items=[])
        summary = f"最近的事务卡片（本页 {len(views)} 张）：\n"
        summary += "\n".join(
            f"#{v['id']} {v['category']}·{v['purpose']} [{v['status']}] {v['note']}" for v in views
        )
        if total and args.offset + len(views) < total:
            summary += (
                f"\n还有 {total - args.offset - len(views)} 张未显示，"
                f"如需继续请用 offset={args.offset + len(views)} 查询下一页。"
            )
        return summary, CardList(items=[CardView(**v) for v in views])

    return [create_tracking_card, create_cards_from_query, complete_tracking_card, list_tracking_cards]
