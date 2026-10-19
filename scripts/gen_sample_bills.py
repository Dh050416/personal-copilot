"""生成脱敏示例账单 samples/sample_bills.xlsx。

用途：让新用户不必拿自己的真实账单就能跑通 README 的「第一轮实验」。

为什么需要脚本而不是直接提交一个 xlsx：
- 示例数据必须**完全虚构**（商户、姓名、金额都是编的），脚本让这一点可被审查与复现；
- 日期需要相对「今天」生成，否则时间范围筛选的实验很快就会失效。

用法（需先装好后端依赖里的 openpyxl）：

    python scripts/gen_sample_bills.py                  # 默认 60 条，写 samples/sample_bills.xlsx
    python scripts/gen_sample_bills.py --count 120      # 指定条数
    python scripts/gen_sample_bills.py --out /tmp/a.xlsx

表头沿用微信账单习惯，`app/services/importer.py` 的 ALIASES 能直接识别。
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

# 全部为虚构名称。刻意带上「示例 / 演示」前缀，避免与真实商户混淆。
MERCHANTS = {
    '餐饮': ['示例咖啡馆', '演示面馆', '示例烘焙坊', '演示火锅店', '示例快餐店'],
    '交通': ['示例地铁出行', '演示加油站', '示例网约车', '演示停车费'],
    '购物': ['示例电商', '演示超市', '示例书店', '演示家居店'],
    '订阅': ['示例视频会员', '演示云盘会员', '示例音乐会员'],
    '医疗': ['示例药店', '演示口腔诊所'],
    '住房': ['示例物业', '演示水电缴费'],
    '教育': ['示例在线课程', '演示书店教材'],
}
# 通用占位人名，用于演示「查给某人的转账」
PEOPLE = ['张三', '李四', '王五']

PRODUCTS = ['消费', '订单支付', '会员续费', '充值', '扫码付款']
PAY_METHODS = ['零钱', '示例银行(1234)', '演示信用卡(5678)']
STATUSES = ['支付成功', '已存入零钱', '对方已收钱']
TX_TYPES = {'消费': '商户消费', '转账': '转账', '订阅': '商户消费'}

HEADERS = ['交易时间', '交易类型', '交易对方', '商品', '收/支', '金额(元)',
           '支付方式', '当前状态', '交易单号', '商户单号', '备注', '分类']


def build_rows(count: int, seed: int = 20260101, with_category: bool = True) -> list[list]:
    rng = random.Random(seed)
    rows: list[list] = []
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    serial = 1

    def push(tx_time: datetime, tx_type: str, merchant: str, direction: str, amount: float,
             category: str = '其他', note: str = ''):
        nonlocal serial
        row = [
            tx_time,
            tx_type,
            merchant,
            rng.choice(PRODUCTS),
            direction,
            round(amount, 2),
            rng.choice(PAY_METHODS),
            rng.choice(STATUSES),
            f'SAMPLE{tx_time.strftime("%Y%m%d")}{serial:04d}',
            f'M{rng.randrange(10**12):012d}',
            note,
        ]
        if with_category:
            row.append(category)
        rows.append(row)
        serial += 1

    # 1) 消费支出：覆盖各分类，分布在最近 60 天
    categories = list(MERCHANTS)
    for _ in range(int(count * 0.7)):
        category = rng.choice(categories)
        merchant = rng.choice(MERCHANTS[category])
        tx_time = today - timedelta(days=rng.randrange(0, 60),
                                    hours=rng.randrange(8, 22),
                                    minutes=rng.randrange(0, 60))
        amount = round(rng.uniform(8, 320), 2)
        push(tx_time, '商户消费', merchant, '支出', amount, category)

    # 2) 转账：用于实验「查给张三的转账」
    for _ in range(int(count * 0.15)):
        person = rng.choice(PEOPLE)
        tx_time = today - timedelta(days=rng.randrange(0, 45),
                                    hours=rng.randrange(9, 21),
                                    minutes=rng.randrange(0, 60))
        push(tx_time, '转账', person, '支出', round(rng.uniform(50, 800), 2), '其他', '示例转账')

    # 3) 收入：工资与退款，用于验证方向筛选
    for _ in range(max(1, int(count * 0.08))):
        tx_time = today - timedelta(days=rng.randrange(0, 60), hours=rng.randrange(9, 18))
        push(tx_time, '转账', '示例公司代发', '收入', round(rng.uniform(3000, 9000), 2),
             '其他', '示例工资')
    for _ in range(max(1, int(count * 0.07))):
        tx_time = today - timedelta(days=rng.randrange(0, 30), hours=rng.randrange(9, 20))
        push(tx_time, '商户消费', rng.choice(MERCHANTS['购物']), '收入',
             round(rng.uniform(20, 200), 2), '购物', '示例退款')

    rows.sort(key=lambda row: row[0])
    return rows


def write_xlsx(rows: list[list], out: Path, with_category: bool = True) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '示例账单'
    sheet.append(HEADERS if with_category else HEADERS[:-1])
    for row in rows:
        sheet.append(row)
    # 交易单号必须是文本：Excel 若存成数值会丢精度，importer 会直接拒绝
    for row in sheet.iter_rows(min_row=2, min_col=9, max_col=9):
        for cell in row:
            cell.number_format = '@'
    out.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(out)


def main() -> None:
    parser = argparse.ArgumentParser(description='生成脱敏示例账单')
    parser.add_argument('--count', type=int, default=60, help='生成条数（默认 60）')
    parser.add_argument('--out', type=str, default=None, help='输出路径')
    parser.add_argument('--no-category', action='store_true',
                        help='不写「分类」列，让导入时的规则/模型分类介入（用于实验批量分类）')
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / 'samples' / 'sample_bills.xlsx'
    with_category = not args.no_category
    rows = build_rows(args.count, with_category=with_category)
    write_xlsx(rows, out, with_category)
    print(f'已生成 {len(rows)} 条示例账单：{out}')
    print('注意：数据全部虚构，请勿替换为真实账单后提交到仓库。')


if __name__ == '__main__':
    main()
