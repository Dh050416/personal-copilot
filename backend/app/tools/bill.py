"""bill tool adapters; business persistence lives in services."""

from datetime import datetime, time
from decimal import Decimal

from langchain_core.tools import tool
from sqlalchemy import func, select

from app.data.models import Bill
from app.data.views import bill_view
from app.schemas import (
    AddBillArgs,
    BillView,
    QueryBillArgs,
    QueryBillResult,
)
from app.services.bill_service import add_bill as service_add_bill
from app.tools.context import _IDEMPOTENCY_KEY, current_user_id


def build_bill_tools(db, embedding_client=None):
    @tool("query_bill", args_schema=QueryBillArgs, response_format="content_and_artifact")
    async def query_bill(**kwargs):
        """查询历史账单，按日期、消费分类、商户/转账对方、金额、支付方式、交易状态及交易类型筛选。

        转账记录用 transaction_type=转账；给某人用 merchant 及 direction=expense，收到某人用 income，
        往来双方用 direction=null。不是发起转账。raw口径total为原始金额合计，consumption口径为扣除已知退款的消费净额。
        明细最多100条且金额始终保留原始值；
        结果较多时分页：用 offset 跳过已看部分、limit 控制每页条数。
        """
        args = QueryBillArgs.model_validate(kwargs)
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
        if args.category:
            filters.append(Bill.category == args.category)
        if args.merchant:
            filters.append(
                Bill.merchant == args.merchant
                if args.merchant_match == "exact"
                else Bill.merchant.contains(args.merchant, autoescape=True)
            )
        if args.transaction_type:
            filters.append(Bill.transaction_type == args.transaction_type)
        if args.payment_method:
            filters.append(Bill.payment_method.contains(args.payment_method, autoescape=True))
        if args.transaction_status:
            filters.append(Bill.transaction_status.contains(args.transaction_status, autoescape=True))
        if args.min_amount is not None:
            cents = int(args.min_amount * 100)
            filters.append(
                Bill.amount_cents >= cents if args.min_amount_inclusive else Bill.amount_cents > cents
            )
        if args.max_amount is not None:
            cents = int(args.max_amount * 100)
            filters.append(
                Bill.amount_cents <= cents if args.max_amount_inclusive else Bill.amount_cents < cents
            )
        if args.direction and args.direction != "neutral":
            filters.append(Bill.direction == args.direction)
        from app.services.bill_accounting import effective_consumption

        amount_expression = Bill.amount_cents
        if args.accounting_mode == "consumption":
            filters.extend([Bill.nature == "consumption", Bill.direction == "expense"])
            amount_expression = effective_consumption()
        async with db.sessions() as session:
            count, total = (
                await session.execute(
                    select(func.count(Bill.id), func.coalesce(func.sum(amount_expression), 0)).where(*filters)
                )
            ).one()
            bills = list(
                await session.scalars(
                    select(Bill)
                    .where(*filters)
                    .order_by(Bill.tx_time.desc(), Bill.id.desc())
                    .offset(args.offset)
                    .limit(args.limit)
                )
            )
        warnings = []
        if args.accounting_mode == "consumption":
            warnings.append(
                "消费统计已扣除已知退款，按原交易日期归属；明细仍展示原始金额。转账及性质不明记录不计入消费。"
            )
        if args.category:
            warnings.append("消费分类含自动推断，未分类记录归为其他；分类筛选可能遗漏未正确分类的记录。")
        async with db.sessions() as session:
            for key, label in (("payment_method", "支付方式"), ("transaction_status", "交易状态")):
                if getattr(args, key) and await session.scalar(
                    select(func.count(Bill.id)).where(Bill.user_id == user_id, getattr(Bill, key) == "未知")
                ):
                    warnings.append(
                        f"库中部分旧记录缺少{label}，请重新导入原始账单补齐；本次筛选不包含缺失记录。"
                    )
        total_text = format(Decimal(total) / Decimal(100), ".2f")
        result = QueryBillResult(
            count=count,
            total=total_text,
            returned_count=len(bills),
            truncated=count > len(bills),
            filters=args.model_dump(mode="json"),
            items=[bill_view(b) for b in bills],
            warnings=warnings,
        )
        amount_label = "消费净支出" if args.accounting_mode == "consumption" else "记录金额合计"
        summary = (
            f"查询范围：{scope_text(args)}，共 {count} 笔，{amount_label} {total_text} 元，"
            f"返回第 {args.offset + 1}-{args.offset + len(bills)} 条明细（{len(bills)} 条）；"
            + (
                "消费净支出已扣除已知退款，明细为原始金额。"
                if args.accounting_mode == "consumption"
                else "按原始账单金额统计，未做退款抵扣。"
            )
        )
        if bills:
            summary += "\n明细（如需建卡关联，用 ID 字段）："
            for b in bills[:10]:
                summary += (
                    f"\n- ID {b.id}｜{b.tx_time}｜{b.merchant}｜¥{format(b.amount_cents / 100, '.2f')}"
                    f"｜{b.transaction_type}｜{b.category}"
                )
        if count > args.offset + len(bills):
            summary += (
                f"\n还有 {count - args.offset - len(bills)} 条未显示，"
                f"如需继续请用 offset={args.offset + len(bills)} 查询下一页。"
            )
        if warnings:
            summary += "\n提示：" + "；".join(warnings)
        return summary, result

    @tool("add_bill", args_schema=AddBillArgs, response_format="content_and_artifact")
    async def add_bill(**kwargs):
        """随手记：手动录入一笔账单（不经过文件导入），必须经用户确认。

        用于「刚发生的消费/转账、没有账单文件可导入」的场景，如「刚吃饭 AA 我付了 80 元」。
        交易方向用 direction 表达（expense 支出 / income 收入），金额恒为正数；
        「刚才/今天」等相对时间先换算成具体时间再传 occurred_at。
        """
        args = AddBillArgs.model_validate(kwargs)
        view = await service_add_bill(db, current_user_id(), args, _IDEMPOTENCY_KEY.get())
        sign = "-" if view["direction"] == "expense" else "+"
        summary = (
            f"已记一笔{view['direction'] == 'expense' and '支出' or '收入'}："
            f"{sign}¥{view['amount']}（{view['category']}"
            + (f"，{view['merchant']}" if view["merchant"] else "")
            + f"，时间 {view['tx_time']}）"
        )
        if view["note"]:
            summary += f"；备注：{view['note']}"
        summary += "。"
        return summary, BillView(**view)

    return [query_bill, add_bill]


def scope_text(args):
    if args.start_date and args.end_date and args.start_date == args.end_date:
        return str(args.start_date)
    if args.start_date and args.end_date:
        return f"{args.start_date} 至 {args.end_date}"
    if args.start_date:
        return f"{args.start_date} 起"
    if args.end_date:
        return f"截至 {args.end_date}"
    return "全部时间"
