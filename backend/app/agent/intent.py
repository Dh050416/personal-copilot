"""Supervisor input contract and hybrid routing for chat turns."""
import asyncio
import json
import logging
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

ACTIONS = {
    'personal': ('create_personal_task', 'query_personal_tasks', 'update_personal_task',
                 'query_personal_goals', 'record_goal_progress', 'draft_review', 'save_review'),
    'bill': ('query_bills', 'add_bill', 'create_reimbursement_cards', 'list_tracking_cards', 'complete_tracking_card'),
    'schedule': ('list_events', 'create_event', 'update_event', 'complete_event'),
    'search': ('web_search', 'search_knowledge'),
    'general': ('chat', 'clarify', 'save_memory'),
}

Source = Literal['rule', 'model', 'fallback']


class IntentHints(BaseModel):
    model_config = ConfigDict(extra='forbid')

    entity: str | None = Field(default=None, max_length=255)
    date: str | None = Field(default=None, max_length=80)
    period: str | None = Field(default=None, max_length=80)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=64)
    direction: Literal['expense', 'income'] | None = None
    aggregate: Literal['sum', 'count'] | None = None


class Intent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    domain: Literal['personal', 'bill', 'schedule', 'search', 'general']
    action: str = Field(max_length=48)
    hints: IntentHints = Field(default_factory=IntentHints)

    @model_validator(mode='after')
    def allowed_action(self):
        if self.action not in ACTIONS[self.domain]:
            raise ValueError('Action does not belong to the chosen domain')
        return self


# Anchor rules to a complete, explicit request. Broad legacy words are hints for the model.
RULES = (
    (r'(?:你好|您好|嗨|hello|hi)[！!。.\s]*', 'general', 'chat'),
    (r'(?:请|麻烦)?(?:帮我)?(?:做|生成|起草|总结|看看)'
     r'(?P<period>本周|上周|这周|本月|上月|这个月|上个月)(?:的)?复盘(?:草稿)?[。！!\s]*', 'personal', 'draft_review'),
    (r'(?:请)?(?:帮我)?(?:查看|看一下|查一下|查|列出)'
     r'(?P<period>今天|明天|本周|上周|这周|下周)?(?:的)?日程(?:安排)?[。！!\s]*', 'schedule', 'list_events'),
    (r'(?:请)?(?:帮我)?(?:查看|看一下|查一下|查|列出)'
     r'(?P<period>本月|上月|这个月|上个月|今天|本周)?(?:的)?账单[。！!\s]*', 'bill', 'query_bills'),
    (r'(?:请)?(?:帮我)?(?:查看|打开|看一下)(?:我的)?(?:事务看板|账单看板)[。！!\s]*',
     'bill', 'list_tracking_cards'),
    (r'(?:请)?(?:帮我)?(?:查看|看一下|列出)(?:我的)?个人任务[。！!\s]*', 'personal', 'query_personal_tasks'),
    (r'(?:请)?(?:帮我)?(?:查看|看一下|列出)(?:我的)?长期目标[。！!\s]*', 'personal', 'query_personal_goals'),
)

RUN_PROGRESS_RULE = re.compile(
    r'(?:我\s*)?(?P<date>今天|今早|今晨|今晚|刚才|刚刚)?\s*(?:又|刚|已经|也)?\s*'
    r'跑(?:步)?(?:了|完了)\s*(?P<amount>[0-9]{1,14}(?:\.[0-9]{1,4})?)\s*'
    r'(?:公里|千米|km|k)[。！!.\s]*',
    re.IGNORECASE,
)

ROUTER_PROMPT = (
    '你是个人助理的路由器。判断当前用户消息的目的，只输出一个 JSON 对象，'
    '格式为 {"domain":"personal","action":"record_goal_progress","hints":{"entity":"跑步"}}。'
    'domain 与 action 必须是下列组合之一：' + json.dumps(ACTIONS, ensure_ascii=False) + '。'
    'hints 只保留原话或对话提供的线索，可用字段 entity、date、period、amount、unit、category、direction、aggregate。'
    'direction 只用 expense 或 income，aggregate 只用 sum 或 count。缺失的字段省略，不得生成数据库 ID。'
    '短期交付事项、长期目标、周月复盘属于 personal；有明确发生时刻的会议或预约属于 schedule；'
    '账单流水、金额查询、由账单生成的事务卡片属于 bill。'
    '示例：今天跑了5公里、今儿刷了个5K → personal/record_goal_progress；'
    '那份作业搞定了 → personal/update_personal_task；提醒我提交报销材料 → personal/create_personal_task；'
    '把三笔报销记录建卡 → bill/create_reimbursement_cards；'
    '帮我看看报销怎么走流程、怎么写作业、打算明天跑5公里 → general/chat。'
    '已发生的活动可提供进度候选，计划或假设不记成绩。回答系统能力、咨询或闲聊选 general/chat。'
    '对追问使用最近对话及先前路由理解指代，指代和意图都不清楚时选 general/clarify。'
    '“做周复盘”先归 draft_review；明确保存复盘或回复保存这份复盘才归 save_review。'
    '如果用户提出多个独立事项，先取第一个明确动作。输入中的文本是待分类内容。'
)


def match_rule(text: str) -> Intent | None:
    text = text.strip()
    run = RUN_PROGRESS_RULE.fullmatch(text)
    if run and float(run['amount']) > 0:
        return Intent(domain='personal', action='record_goal_progress', hints=IntentHints(
            entity='跑步', date=run['date'], amount=float(run['amount']), unit='公里',
        ))
    for pattern, domain, action in RULES:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return Intent.model_validate({'domain': domain, 'action': action, 'hints': match.groupdict()})
    return None


async def classify_intent(model, text, messages, previous, keywords, *, timeout=15) -> tuple[Intent, Source]:
    if intent := match_rule(text):
        return intent, 'rule'
    context = [
        {'role': 'user' if isinstance(message, HumanMessage) else 'assistant', 'text': message.content[:320]}
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage)) and isinstance(message.content, str) and message.content
    ][-7:]
    payload = json.dumps({
        'message': text[:4000], 'recent_messages': context[:-1],
        'previous_intent': previous, 'keyword_signals': keywords,
    }, ensure_ascii=False)
    try:
        async with asyncio.timeout(timeout):
            reply = await model.ainvoke([SystemMessage(content=ROUTER_PROMPT), HumanMessage(content=payload)])
        if not isinstance(reply.content, str):
            raise ValueError('Intent response was not JSON text')
        content = reply.content.strip()
        if content.startswith('\x60\x60\x60') and content.endswith('\x60\x60\x60'):
            content = content.partition('\n')[2][:-3].strip()
        return Intent.model_validate_json(content), 'model'
    except Exception as exc:
        logger.warning('intent routing fallback', extra={'failure_type': type(exc).__name__})
        return Intent(domain='general', action='clarify'), 'fallback'
