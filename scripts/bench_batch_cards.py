"""批量建卡基准：改前（逐张 create_tracking_card ×N）vs 改后（create_cards_from_query ×1）。

用法：..\\venv\\Scripts\\python.exe bench_batch_cards.py --path old|new [--n 40]
口径说明：
- 工具执行耗时 = 真实实测（本地 SQLite，不含模型网络）
- 模型往返估算 = 场景 A: ceil(N/8) 轮 × 2.5s；场景 B: 1 轮 × 2.5s（估算值，标注为估算）
- 审批估算 = 场景 A 每轮 1 次（约 1.5s/次用户确认）；场景 B 1 次
"""
import argparse
import asyncio
import tempfile
import time
from pathlib import Path

from app.agent.tools import build_tools
from app.data.db import Database
from app.data.models import Bill, TrackingCard
from app.schemas import CreateTrackingCardArgs
from app.services.card_service import create_card
from sqlalchemy import select

N_DEFAULT = 40
ROUND_TRIP_SEC = 2.5   # 每轮模型往返估算（DeepSeek API，含网络+生成）
APPROVE_SEC = 1.5      # 每次用户审批确认估算


def make_bill(i: int):
    day = i % 28 + 1
    return Bill(
        tx_no=f'tx-b{i}', tx_time=f'2026-08-{day:02d}T10:0{i % 60:02d}:00',
        merchant=f'收款人{i:03d}', product='转账', amount_cents=10000 + i * 100,
        direction='expense', transaction_type='转账', payment_method='零钱',
        transaction_status='已转账', merchant_tx_no=f'mb{i}', note='',
        category='其他', category_source='rule', category_confidence=.95,
        source='bench', import_batch_id='bench', source_sheet='Sheet1', source_row=i + 1,
    )


async def seed(db, n):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([make_bill(i) for i in range(n)])
    async with db.sessions() as session:
        return list(await session.scalars(select(Bill).where(Bill.transaction_type == '转账')))


async def old_path(db, bills):
    """旧路径：模拟 run_tools 串行调用 create_tracking_card ×N（每卡一个事务）。"""
    start = time.perf_counter()
    for b in bills:
        await create_card(db, CreateTrackingCardArgs(bill_id=b.id, category='其他', purpose='报销'))
    return time.perf_counter() - start


async def new_path(db, tools, bills):
    """新路径：create_cards_from_query 一次调用（单事务批量建卡）。"""
    tool = next(t for t in tools if t.name == 'create_cards_from_query')
    start = time.perf_counter()
    msg = await tool.arun({
        'start_date': '2026-08-01', 'end_date': '2026-08-31',
        'transaction_type': '转账', 'direction': 'expense',
        'card_category': '其他', 'purpose': '报销',
    })
    elapsed = time.perf_counter() - start
    async with db.sessions() as session:
        cards = list(await session.scalars(select(TrackingCard).where(TrackingCard.category == '其他')))
    return elapsed, {'total': len(cards), 'card_ids': [c.id for c in cards][:5]}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', choices=['old', 'new'], required=True)
    parser.add_argument('--n', type=int, default=N_DEFAULT)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / 'bench.db')
        await db.initialize()
        bills = await seed(db, args.n)
        n = len(bills)
        tools = build_tools(db)

        if args.path == 'old':
            elapsed = await old_path(db, bills)
            rounds = (n + 7) // 8
            total = elapsed + rounds * ROUND_TRIP_SEC + rounds * APPROVE_SEC
            print(f'old_path: n={n} tool_exec={elapsed:.3f}s '
                  f'model_rounds={rounds}(估) total≈{total:.1f}s(估)')
        else:
            elapsed, data = await new_path(db, tools, bills)
            total = elapsed + 1 * ROUND_TRIP_SEC + 1 * APPROVE_SEC
            print(f'new_path: n={n} tool_exec={elapsed:.3f}s model_rounds=1 '
                  f'total≈{total:.1f}s(估) cards={data["total"]} ids={data["card_ids"]}...')
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
