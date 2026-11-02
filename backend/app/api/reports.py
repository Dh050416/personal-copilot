"""消费报表只读 REST 端点：纯 SQL 聚合，供前端「消费报表」页直连（不走 Agent，省 token）。

聚合口径与 Agent 工具 query_bill 保持一致：
- 金额单位：分（amount_cents），返回时转元字符串（两位小数）；
- 支出（direction=expense）为负向口径，收入（income）为正向口径，分别统计；
- 分类占比只统计「支出」方向（分类是消费概念，收入不参与分类占比）。
"""
from datetime import date as date_type

from fastapi import APIRouter, Request
from sqlalchemy import case, func, select

from app.data.models import Bill
from app.schemas import ReportSummary

router = APIRouter(prefix='/api/reports', tags=['reports'])


def _cents_to_yuan(cents: int) -> str:
    from decimal import Decimal
    return format(Decimal(cents) / Decimal(100), '.2f')


@router.get('/summary', response_model=ReportSummary)
async def summary(request: Request,
                  start_date: date_type | None = None,
                  end_date: date_type | None = None):
    """汇总：区间内总支出/总收入/笔数、分类占比、按月收支趋势。

    start_date/end_date 为日期闭区间（按 tx_time 前缀过滤）；缺省统计全部账单。
    """
    filters = [Bill.user_id == request.state.user_id]
    if start_date is not None:
        filters.append(Bill.tx_time >= start_date.isoformat())
    if end_date is not None:
        filters.append(Bill.tx_time <= end_date.isoformat() + 'T23:59:59')

    async with request.app.state.db.sessions() as session:
        # 总支出 / 总收入 / 笔数
        totals = (await session.execute(
            select(
                func.coalesce(func.sum(
                    case((Bill.direction == 'expense', Bill.amount_cents), else_=0)), 0),
                func.coalesce(func.sum(
                    case((Bill.direction == 'income', Bill.amount_cents), else_=0)), 0),
                func.count(Bill.id),
            ).where(*filters)
        )).one()
        total_expense, total_income, bill_count = totals

        # 分类占比（仅支出方向）
        cat_rows = list(await session.execute(
            select(Bill.category, func.coalesce(func.sum(Bill.amount_cents), 0))
            .where(Bill.direction == 'expense', *filters)
            .group_by(Bill.category)
            .order_by(func.sum(Bill.amount_cents).desc())
        ))

        # 按月趋势：tx_time 形如 YYYY-MM-DDTHH:MM:SS 或 YYYY-MM-DD，取前 7 位（YYYY-MM）
        month_rows = list(await session.execute(
            select(
                func.substr(Bill.tx_time, 1, 7).label('month'),
                func.coalesce(func.sum(
                    case((Bill.direction == 'expense', Bill.amount_cents), else_=0)), 0),
                func.coalesce(func.sum(
                    case((Bill.direction == 'income', Bill.amount_cents), else_=0)), 0),
            ).where(*filters).group_by('month').order_by('month')
        ))

    categories = [
        {'category': c, 'amount': _cents_to_yuan(a), 'amount_cents': a}
        for c, a in cat_rows
    ]
    monthly = [
        {'month': m, 'expense': _cents_to_yuan(e), 'income': _cents_to_yuan(i),
         'expense_cents': e, 'income_cents': i}
        for m, e, i in month_rows
    ]
    return ReportSummary(
        total_expense=_cents_to_yuan(total_expense),
        total_income=_cents_to_yuan(total_income),
        bill_count=bill_count,
        total_expense_cents=total_expense,
        total_income_cents=total_income,
        categories=categories,
        monthly=monthly,
    )
