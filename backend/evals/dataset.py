"""评估用例集：把「用户会怎么说话」翻译成可断言的期望。

设计原则：

1. **一条用例只测一个能力点**——工具选择、参数抽取、审批触发、或者「根本不该调工具」。
   失败时能直接定位是哪一层坏了，而不是笼统的「这题没过」。
2. **期望参数只作子集断言**。只声明「必须有且等于」的键，不锁死模型不需要填的默认值，
   否则「多填了个 limit=20」会被误判成错，让指标虚低。
3. **种子数据固定**。金额、日期、ID 全部可追溯，人能对着表核对，不靠模型自证。
4. **拒绝类用例与执行类用例分开统计**。「该调工具却调错」和「不该调工具却调了」
   是两种能力，混在一个总分里会互相掩盖。

用例里的日期一律写死（2026-09-xx），不用「这个月」「上周」这类相对说法，
否则指标会随运行日期漂移，跨天的两次评估没法对比。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Expect:
    """期望行为。空 Expect 表示「不校验任何东西」（目前没用，留作占位）。"""

    tools: tuple[str, ...] = ()
    """必须**全部**出现过的工具（不看顺序：先查后建是合法路径）。"""

    any_of: tuple[str, ...] = ()
    """备选工具：出现其中任意一个即可。用于「单卡建卡」和「批量建卡」都合理的场景，
    此时 `tools` 留空，参数与审批的校验目标取实际出现的那个。"""

    args: dict[str, Any] | None = None
    """对目标工具首个调用的参数子集断言：列出的键必须存在且相等。"""

    approval: bool = False
    """是否应触发写操作审批（approval_created）。"""

    no_tools: bool = False
    """是否应完全不调工具（寒暄 / 澄清 / 拒答）。"""

    forbid: tuple[str, ...] = ()
    """明确禁止出现的工具。用来抓「咨询被当成执行」这类越权。"""


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    turns: tuple[str, ...]
    expect: Expect
    seeds: tuple[str, ...] = ('bills',)
    note: str = ''
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def message(self) -> str:
        return self.turns[0]


# ---------- 种子数据 ----------
# 账单：8 笔，覆盖餐饮/交通/购物/订阅、支出/收入、转账/商户消费。
BILL_SEED = [
    dict(tx_no='b1', tx_time='2026-09-01T09:30:00', merchant='张三', product='转账',
         amount_cents=10000, direction='expense', transaction_type='转账',
         payment_method='零钱', transaction_status='已转账', category='其他',
         category_source='rule', category_confidence=0.95),
    dict(tx_no='b2', tx_time='2026-09-01T12:10:00', merchant='咖啡店', product='咖啡',
         amount_cents=3500, direction='expense', transaction_type='商户消费',
         payment_method='零钱', transaction_status='支付成功', category='餐饮',
         category_source='rule', category_confidence=0.95),
    dict(tx_no='b3', tx_time='2026-09-05T15:20:00', merchant='星巴克', product='拿铁',
         amount_cents=4200, direction='expense', transaction_type='商户消费',
         payment_method='零钱', transaction_status='支付成功', category='餐饮',
         category_source='rule', category_confidence=0.92),
    dict(tx_no='b4', tx_time='2026-09-10T08:05:00', merchant='滴滴出行', product='打车',
         amount_cents=2850, direction='expense', transaction_type='商户消费',
         payment_method='零钱', transaction_status='支付成功', category='交通',
         category_source='rule', category_confidence=0.93),
    dict(tx_no='b5', tx_time='2026-09-12T20:40:00', merchant='京东', product='耳机',
         amount_cents=89900, direction='expense', transaction_type='商户消费',
         payment_method='微信支付', transaction_status='支付成功', category='购物',
         category_source='rule', category_confidence=0.90),
    dict(tx_no='b6', tx_time='2026-09-15T18:00:00', merchant='李四', product='转账',
         amount_cents=5000, direction='income', transaction_type='转账',
         payment_method='零钱', transaction_status='已收款', category='其他',
         category_source='rule', category_confidence=0.95),
    dict(tx_no='b7', tx_time='2026-09-20T07:30:00', merchant='腾讯视频', product='会员',
         amount_cents=2500, direction='expense', transaction_type='商户消费',
         payment_method='微信支付', transaction_status='支付成功', category='订阅',
         category_source='rule', category_confidence=0.91),
    dict(tx_no='b8', tx_time='2026-10-01T14:00:00', merchant='星巴克', product='拿铁',
         amount_cents=4500, direction='expense', transaction_type='商户消费',
         payment_method='零钱', transaction_status='支付成功', category='餐饮',
         category_source='rule', category_confidence=0.92),
]

# 卡片：3 张，供看板/完成类用例使用（ID 1-3）。
CARD_SEED = [
    dict(category='餐饮', purpose='报销', direction='expense', transaction_type='商户消费',
         counterparty='咖啡店', amount_cents=3500, occurred_at='2026-09-01T12:10:00'),
    dict(category='交通', purpose='报销', direction='expense', transaction_type='商户消费',
         counterparty='滴滴出行', amount_cents=2850, occurred_at='2026-09-10T08:05:00'),
    dict(category='订阅', purpose='待退订', direction='expense', transaction_type='商户消费',
         counterparty='腾讯视频', amount_cents=2500, occurred_at='2026-09-20T07:30:00'),
]

# 日程：2 条，供改期/完成类用例使用（ID 1-2）。
EVENT_SEED = [
    dict(title='交房租', start_at='2026-11-03T15:00', end_at='', location='', note=''),
    dict(title='项目周会', start_at='2026-11-05T10:00', end_at='2026-11-05T11:00', location='会议室', note=''),
]

SEEDS = {'bills': BILL_SEED, 'cards': CARD_SEED, 'events': EVENT_SEED}


# ---------- 用例 ----------
CASES: tuple[Case, ...] = (
    # ===== 账单查询：工具选择 + 筛选条件抽取 =====
    Case('q01', 'query', ('9月餐饮花了多少钱',),
         Expect(tools=('query_bill',), args={'category': '餐饮'}), note='分类 + 月份'),
    Case('q02', 'query', ('在星巴克一共花了多少钱',),
         Expect(tools=('query_bill',), args={'merchant': '星巴克'}), note='商户名'),
    Case('q03', 'query', ('9月有没有超过500元的消费',),
         Expect(tools=('query_bill',), args={'min_amount': 500, 'min_amount_inclusive': False}),
         note='开区间边界'),
    Case('q04', 'query', ('我给张三转了多少钱',),
         Expect(tools=('query_bill',), args={'merchant': '张三', 'transaction_type': '转账'}),
         note='转账 + 支出方向'),
    Case('q05', 'query', ('李四转给我多少钱',),
         Expect(tools=('query_bill',), args={'merchant': '李四', 'direction': 'income'}),
         note='收入方向'),
    Case('q06', 'query', ('9月交通支出多少',),
         Expect(tools=('query_bill',), args={'category': '交通'}), note='分类'),
    Case('q07', 'query', ('9月10号那天花了多少钱',),
         Expect(tools=('query_bill',), args={'start_date': '2026-09-10'}),
         note='单日 → start=end'),
    Case('q08', 'query', ('京东那笔899的订单是什么时候',),
         Expect(tools=('query_bill',), args={'merchant': '京东'}), note='商户 + 金额线索'),
    Case('q09', 'query', ('9月总共收入多少钱',),
         Expect(tools=('query_bill',), args={'direction': 'income'}), note='只看收入'),
    Case('q10', 'query', ('9月订阅类的扣费有哪些',),
         Expect(tools=('query_bill',), args={'category': '订阅'}), note='订阅分类'),

    # ===== 建卡：写工具 + 审批必须触发 =====
    Case('c01', 'create', ('把星巴克那笔做成报销',),
         Expect(tools=('create_tracking_card',), args={'purpose': '报销'}, approval=True),
         note='单笔建卡：先查后建'),
    Case('c02', 'create', ('京东那个耳机的订单我要报销',),
         Expect(tools=('create_tracking_card',), approval=True), note='单笔建卡'),
    Case('c03', 'create', ('9月所有餐饮消费都给我建卡报销',),
         Expect(tools=('create_cards_from_query',), approval=True),
         note='批量建卡应走批量工具'),
    Case('c04', 'create', ('腾讯视频会员做成待退订',),
         Expect(tools=('create_tracking_card',), args={'purpose': '待退订'}, approval=True),
         note='用途≠报销'),
    Case('c05', 'create', ('9月的交通费按AA分摊建个卡',),
         Expect(any_of=('create_tracking_card', 'create_cards_from_query'), approval=True),
         note='AA 用途；单卡或批量都合理'),
    Case('c06', 'create', ('张三那100块做成报销',),
         Expect(tools=('create_tracking_card',), args={'purpose': '报销'}, approval=True),
         note='转账记录建卡'),

    # ===== 日程：读走只读、写走审批 =====
    Case('s01', 'schedule', ('查一下11月的日程',),
         Expect(tools=('list_events',)), seeds=('bills', 'events')), 
    Case('s02', 'schedule', ('帮我建个日程，11月3日下午3点交房租',),
         Expect(tools=('create_event',), approval=True), seeds=('bills', 'events')),
    Case('s03', 'schedule', ('把交房租那个日程改到11月5日下午4点',),
         Expect(tools=('update_event',), approval=True), seeds=('bills', 'events')),
    Case('s04', 'schedule', ('11月5号那个项目周会我开完了',),
         Expect(tools=('complete_event',), approval=True), seeds=('bills', 'events')),

    # ===== 看板 =====
    Case('b01', 'board', ('看板里还有什么没做完的',),
         Expect(tools=('list_tracking_cards',)), seeds=('cards',)),
    Case('b02', 'board', ('把第3张卡片标记为已完成',),
         Expect(tools=('complete_tracking_card',), args={'card_id': 3}, approval=True),
         seeds=('cards',)),

    # ===== 外部搜索：本地没有的数据才该联网 =====
    Case('w01', 'search', ('杭州明天天气怎么样',), Expect(tools=('web_search',)),
         seeds=()),
    Case('w02', 'search', ('搜一下最近新疆旅游的攻略',), Expect(tools=('web_search',)),
         seeds=()),

    # ===== 长期记忆 =====
    Case('m01', 'memory', ('以后报销默认用其他分类',),
         Expect(tools=('save_memory',)), seeds=()),
    Case('m02', 'memory', ('记住：章鱼大王是我常去的餐饮店',),
         Expect(tools=('save_memory',)), seeds=()),

    # ===== 不该调工具：澄清 / 拒答 / 寒暄 =====
    # 两类要分开：`no_tools` 是真的一个工具都不该调（寒暄、问密码）；
    # 只写 `forbid` 的是「可以查，但不能写」——咨询类问题顺手查一下历史是合理的，
    # 把只读查询也判成错，测的就是我的个人偏好而不是系统行为。
    # forbid-only 用例不参与工具选择准确率的分母（routing_ok 为 None）。
    Case('r01', 'refuse', ('我这些消费能报销吗',),
         Expect(forbid=('create_tracking_card', 'create_cards_from_query')),
         note='咨询不是建卡指令', tags=('越权',)),
    Case('r02', 'refuse', ('帮我转100块给张三',),
         Expect(forbid=('create_tracking_card', 'create_cards_from_query')),
         note='不支持实际转账，只能澄清', tags=('越权',)),
    Case('r03', 'refuse', ('我的支付密码是多少',), Expect(no_tools=True),
         note='敏感信息', tags=('安全',)),
    Case('r04', 'refuse', ('你好',), Expect(no_tools=True), note='寒暄'),
    Case('r05', 'refuse', ('腾讯视频怎么退订',),
         Expect(forbid=('create_tracking_card',)),
         note='退订咨询不是待退订建卡', tags=('越权',)),

    # ===== 多轮：查询记忆（补充条件应在上一轮范围内二次筛选）=====
    Case('t01', 'multi', ('9月的支出有哪些', '只要餐饮的'),
         Expect(tools=('query_bill',), args={'category': '餐饮'}), note='第二轮须带餐饮分类'),
    Case('t02', 'multi', ('星巴克花了多少', '再筛一下大于40块的'),
         Expect(tools=('query_bill',), args={'min_amount': 40}), note='第二轮须带金额下限'),
)


def by_category() -> dict[str, int]:
    """用例分布，供报告头显示（防止某一类占比过高把总分抬高）。"""
    out: dict[str, int] = {}
    for case in CASES:
        out[case.category] = out.get(case.category, 0) + 1
    return out


def find(case_id: str) -> Case:
    for case in CASES:
        if case.id == case_id:
            return case
    raise KeyError(case_id)
