import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128,
                                   description='会话 ID：同一会话的后续消息共用它作为 thread，模型得以衔接上下文；缺省时开启新会话。')


class ResumeRequest(StrictModel):
    approval_id: str
    decision: Literal['approve', 'reject']
    edited_args: dict[str, Any] | None = None


class QueryBillArgs(StrictModel):
    start_date: date | None = None
    end_date: date | None = None
    category: str | None = Field(default=None, max_length=64)
    merchant: str | None = Field(default=None, min_length=1, max_length=255,
                                 description='商户或转账对方的原始名称；按字面包含匹配，不支持模糊身份推断。')
    merchant_match: Literal['contains', 'exact'] = 'contains'
    transaction_type: str | None = Field(default=None, min_length=1, max_length=64,
                                         description='原始交易类型精确匹配，例如转账、商户消费。')
    payment_method: str | None = Field(default=None, min_length=1, max_length=64,
                                       description='支付方式按字面包含匹配，例如零钱、银行卡银行名称。')
    transaction_status: str | None = Field(default=None, min_length=1, max_length=64,
                                           description='交易状态按字面包含匹配，例如支付成功、退款、对方已收钱。')
    min_amount: Decimal | None = Field(default=None, ge=0, le=9999999999.99, decimal_places=2,
                                       description='金额下界，单位元；不含边界时设置min_amount_inclusive=false。')
    max_amount: Decimal | None = Field(default=None, ge=0, le=9999999999.99, decimal_places=2)
    min_amount_inclusive: bool = Field(default=True, strict=True)
    max_amount_inclusive: bool = Field(default=True, strict=True)
    direction: Literal['income', 'expense', 'neutral'] | None = 'expense'
    limit: int = Field(default=20, ge=1, le=100, strict=True,
                       description='单页返回条数上限。')
    offset: int = Field(default=0, ge=0, le=10000, strict=True,
                        description='分页偏移：跳过前 offset 条匹配记录。结果较多时模型可设置 offset 继续取下一页。')

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('start_date 不能晚于 end_date')
        if self.min_amount is not None and self.max_amount is not None:
            if (self.min_amount > self.max_amount or
                (self.min_amount == self.max_amount and not (self.min_amount_inclusive and self.max_amount_inclusive))):
                raise ValueError('金额范围为空或下界大于上界')
        if (not self.min_amount_inclusive and self.min_amount is None or
                not self.max_amount_inclusive and self.max_amount is None):
            raise ValueError('排除金额边界时必须提供相应金额')
        return self


class CreateTrackingCardArgs(StrictModel):
    bill_id: int | None = Field(default=None, ge=1, strict=True,
                                description='关联账单 ID（必须来自查询）。提供时未显式指定的交易属性自动从账单快照。')
    category: str = Field(min_length=1, max_length=64)
    purpose: Literal['报销', '退款', '待退订', 'AA', '待处理']
    direction: Literal['expense', 'income'] | None = Field(
        default=None, description='交易方向：支出 expense / 收入 income。')
    transaction_type: Literal['转账', '商户消费', '二维码', '其他'] | None = Field(
        default=None, description='交易类型，仅接受转账/商户消费/二维码/其他。')
    counterparty: str = Field(default='', max_length=255,
                              description='对方名称：支出=收款人，收入=付款人。')
    amount: Decimal | None = Field(default=None, ge=0, le=9999999999.99, decimal_places=2,
                                   description='金额，单位元；支出与收入均为正数，方向由 direction 表达。')
    occurred_at: str | None = Field(default=None, max_length=32,
                                    description='交易发生时间，ISO 格式，如 2026-09-01T10:00:00。')
    note: str = Field(default='', max_length=500)


class CardBatchRule(StrictModel):
    """分类规则：满足全部指定条件（AND）的记录归入 category。
    按规则列表顺序匹配，第一条命中的生效。"""
    merchant: str | None = Field(default=None, min_length=1, max_length=255,
                                 description='对方名称，按字面包含匹配。')
    transaction_type: str | None = Field(default=None, min_length=1, max_length=64,
                                         description='交易类型精确匹配，例如转账。')
    direction: Literal['income', 'expense'] | None = None
    category: str = Field(min_length=1, max_length=64,
                          description='命中该规则时卡片的账单分类。')


class CreateCardsFromQueryArgs(StrictModel):
    """批量建卡：传查询范围 + 卡片分类，一次调用为所有匹配记录建卡（单事务）。

    交易属性（方向/对方/金额/时间）自动从每笔账单快照复制；
    卡片分类：全部同一分类用 card_category；各张分类不同用 category_rules
    （按对方/类型/方向匹配，顺序命中第一条，未命中回退 card_category）。
    """
    start_date: date | None = None
    end_date: date | None = None
    merchant: str | None = Field(default=None, min_length=1, max_length=255,
                                 description='商户或转账对方名称，按字面包含匹配。')
    transaction_type: str | None = Field(default=None, min_length=1, max_length=64,
                                         description='交易类型精确匹配，例如转账。')
    direction: Literal['income', 'expense'] | None = Field(
        default=None, description='交易方向筛选：支出 expense / 收入 income；不填则不筛。')
    min_amount: Decimal | None = Field(default=None, ge=0, le=9999999999.99, decimal_places=2)
    max_amount: Decimal | None = Field(default=None, ge=0, le=9999999999.99, decimal_places=2)
    limit: int = Field(default=100, ge=1, le=500,
                       description='最多为多少笔匹配记录建卡，防止误操作超量。')
    card_category: str | None = Field(
        default=None, min_length=1, max_length=64,
        description='卡片默认分类（未命中任何规则时兜底），如其他/餐饮/交通。')
    use_bill_category: bool = Field(
        default=False,
        description='分类直接跟随每笔账单已有的分类（账单分类=餐饮→卡片放餐饮泳道）。'
                    '优先级最高；适合「根据账单信息放在不同区域」这类需求。')
    category_rules: list[CardBatchRule] | None = Field(
        default=None, max_length=50,
        description='分类规则：按对方/类型/方向把不同记录分到不同分类；顺序匹配第一条命中。')
    purpose: Literal['报销', '退款', '待退订', 'AA', '待处理'] = '报销'
    note: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('start_date 不能晚于 end_date')
        if self.min_amount is not None and self.max_amount is not None \
                and self.min_amount > self.max_amount:
            raise ValueError('金额下界不能大于上界')
        if not self.card_category and not self.category_rules and not self.use_bill_category:
            raise ValueError('card_category 与 category_rules（或 use_bill_category）至少提供一个')
        return self


class CompleteTrackingCardArgs(StrictModel):
    card_id: int = Field(ge=1, strict=True,
                         description='要标记为已完成的卡片 ID，必须来自 list_tracking_cards 查询结果。')


class SaveMemoryArgs(StrictModel):
    """长期记忆写入：只保存用户明确表达的偏好/事实，key 用简短可读的中文短语。"""
    key: str = Field(..., min_length=1, max_length=128,
                     description='记忆键，简短可读的中文短语，例如「报销默认分类」。')
    value: str = Field(..., min_length=1, max_length=500, description='记忆值，例如「其他」。')


class SaveMemoryResult(StrictModel):
    """save_memory 的输出契约：返回被持久化的记忆键值对。"""
    key: str
    value: str


class EmptyArgs(StrictModel):
    pass


class WebSearchArgs(StrictModel):
    """web_search 参数：保留用户搜索原意，max_results 控制返回条数。"""
    query: str = Field(min_length=1, max_length=300,
                       description='搜索关键词，尽量保留用户原意（中文英文均可），不要自行添加多余修饰词。')
    max_results: int = Field(default=5, ge=1, le=10, strict=True,
                             description='返回结果条数上限。')


class WebSearchItem(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchResult(BaseModel):
    """web_search 输出契约：搜索词、结果数、条目列表与来源。"""
    query: str
    count: int
    provider: str
    items: list[WebSearchItem]


class ListTrackingCardsArgs(StrictModel):
    """事务卡片分页查询参数：按 id 倒序，limit 控制每页条数、offset 翻页。"""
    limit: int = Field(default=50, ge=1, le=100, strict=True,
                       description='单页返回条数上限。')
    offset: int = Field(default=0, ge=0, le=10000, strict=True,
                        description='分页偏移：跳过前 offset 张卡片。结果较多时模型可设置 offset 继续取下一页。')


class BillView(StrictModel):
    id: int
    tx_no: str
    tx_time: str
    merchant: str
    amount: str
    direction: str
    transaction_type: str
    product: str
    payment_method: str
    transaction_status: str
    note: str
    category_source: str
    category_confidence: float
    category: str
    source: str


class QueryBillResult(StrictModel):
    count: int
    total: str
    returned_count: int
    truncated: bool
    filters: dict[str, Any]
    items: list[BillView]
    warnings: list[str] = Field(default_factory=list)


class ConfirmBillCategory(StrictModel):
    category: str = Field(min_length=1, max_length=64)
    confirmed: Literal[True]


class ClassifyBillsRequest(StrictModel):
    limit: int = Field(default=200, ge=1, le=1000, strict=True)


class CardView(StrictModel):
    id: int
    bill_id: int | None
    category: str
    purpose: str
    note: str
    status: str
    direction: str
    transaction_type: str
    counterparty: str
    amount: str | None
    occurred_at: str


class CardList(StrictModel):
    items: list[CardView]


class CardBatchView(StrictModel):
    total: int
    card_ids: list[int]
    total_amount: str
    items: list[CardView]


# ---------- 日程 ----------

_TIME_RE = r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$'


def _normalize_dt(value: str | None, field: str) -> str:
    """日程时间规范化：空值/纯日期 YYYY-MM-DD → 当天 00:00:00；带时间则原样保留（秒可省略）。"""
    if not value:
        return ''
    if not re.fullmatch(_TIME_RE, value):
        raise ValueError(f'{field} 格式应为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM[:SS]，收到：{value}')
    if 'T' in value:
        return value if len(value) > 16 else value + ':00'
    return value + 'T00:00:00'


class ListEventsArgs(StrictModel):
    """日程查询参数：按日期范围与状态筛选，分页。"""
    start_date: date | None = None
    end_date: date | None = None
    status: Literal['active', 'done', 'all'] = 'active'
    limit: int = Field(default=50, ge=1, le=100, strict=True)
    offset: int = Field(default=0, ge=0, le=10000, strict=True)

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('start_date 不能晚于 end_date')
        return self


class CreateEventArgs(StrictModel):
    """新建日程：标题必填，时间必填（YYYY-MM-DD 或 YYYY-MM-DDTHH:MM[:SS]）。"""
    title: str = Field(min_length=1, max_length=255, description='日程标题，如「和客户开会」「看牙医」。')
    start_at: str = Field(min_length=1, max_length=32,
                          description='开始时间，ISO 格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM。')
    end_at: str | None = Field(default=None, max_length=32,
                               description='结束时间（可选），ISO 格式同上；早于 start_at 会报错。')
    location: str = Field(default='', max_length=255, description='地点（可选）。')
    note: str = Field(default='', max_length=500, description='备注（可选）。')

    @model_validator(mode='after')
    def normalized(self):
        self.start_at = _normalize_dt(self.start_at, 'start_at')
        self.end_at = _normalize_dt(self.end_at, 'end_at')
        if self.end_at and self.end_at < self.start_at:
            raise ValueError('end_at 不能早于 start_at')
        return self


class UpdateEventArgs(StrictModel):
    """改期/修改日程：event_id 必填，其余字段至少提供一个。"""
    event_id: int = Field(ge=1, strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    start_at: str | None = Field(default=None, max_length=32)
    end_at: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def normalized(self):
        if self.start_at:
            self.start_at = _normalize_dt(self.start_at, 'start_at')
        if self.end_at:
            self.end_at = _normalize_dt(self.end_at, 'end_at')
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError('end_at 不能早于 start_at')
        if not any((self.title, self.start_at, self.end_at, self.location, self.note)):
            raise ValueError('至少提供一个要修改的字段')
        return self


class CompleteEventArgs(StrictModel):
    """标记日程完成：event_id 必须来自查询结果。"""
    event_id: int = Field(ge=1, strict=True)


class EventView(StrictModel):
    id: int
    title: str
    start_at: str
    end_at: str
    location: str
    note: str
    status: str


class EventList(StrictModel):
    items: list[EventView]
