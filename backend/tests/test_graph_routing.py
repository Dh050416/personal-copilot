import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import select

from app.agent.graph import build_system_message, detect_skills, initial_input, make_graph
from app.agent.tools import WRITE_TOOLS, build_tools
from app.data.db import Database
from app.data.models import Bill, TrackingCard


@pytest.fixture
async def ctx(tmp_path):
    db = Database(tmp_path / 'graph.db')
    await db.initialize()
    tools = build_tools(db)
    yield {'db': db, 'tools': tools}
    await db.close()


async def seed_bills(db):
    async with db.sessions() as session:
        async with session.begin():
            session.add_all([
                Bill(tx_no='a', tx_time='2026-09-01T10:00:00', merchant='张三', product='转账',
                     amount_cents=10000, direction='expense', transaction_type='转账',
                     payment_method='零钱', transaction_status='已转账', merchant_tx_no='ma', note='',
                     category='其他', category_source='unclassified', category_confidence=0,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=1),
                Bill(tx_no='c', tx_time='2026-09-01T12:00:00', merchant='咖啡店', product='咖啡',
                     amount_cents=3500, direction='expense', transaction_type='商户消费',
                     payment_method='零钱', transaction_status='支付成功', merchant_tx_no='mc', note='',
                     category='餐饮', category_source='rule', category_confidence=.95,
                     source='test', import_batch_id='b', source_sheet='Sheet1', source_row=3),
            ])


class FakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel 不支持 bind_tools，测试用子类让绑定静默通过。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """流式入口：复用 _agenerate 产出一个完整 chunk，保证流式路径与普通调用语义一致。"""
        import json

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        response = await self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        msg = response.generations[0].message
        chunk_kwargs = {'content': msg.content or ''}
        if getattr(msg, 'tool_calls', None):
            chunk_kwargs['tool_call_chunks'] = [
                {'name': tc['name'], 'args': json.dumps(tc['args']), 'id': tc['id'], 'index': i}
                for i, tc in enumerate(msg.tool_calls)
            ]
        yield ChatGenerationChunk(message=AIMessageChunk(**chunk_kwargs))


def fake_llm(*messages):
    return FakeChatModel(messages=iter(messages))


def tool_call_message(name, args, call_id='call-1'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args,
                                              'id': call_id, 'type': 'tool_call'}])


def graph_for(llm, tools):
    return make_graph(llm, tools, WRITE_TOOLS, MemorySaver())


def resume(decision, approval_id, edited_args=None):
    payload = {'decision': decision, 'approval_id': approval_id}
    if edited_args is not None:
        payload['edited_args'] = edited_args
    return Command(resume=payload)


def interrupted_payload(result):
    assert '__interrupt__' in result, f'expected interrupt, got {result}'
    return result['__interrupt__'][0].value


async def run_to_end(graph, graph_input, config):
    """循环段式执行（对应 chat.py 的循环 runner）：每次执行到 interrupt 时，
    若是输入检查点（input_checkpoint）则自动 resume(None) 放行继续，直到图跑到 END；
    遇审批等其它 interrupt 则停住返回（由测试显式 resume）。"""
    current = graph_input
    while True:
        result = await graph.ainvoke(current, config)
        auto = False
        for inter in result.get('__interrupt__', []):
            value = getattr(inter, 'value', inter)
            if isinstance(value, dict) and value.get('kind') == 'input_checkpoint':
                auto = True
                break
        if not auto:
            return result
        current = Command(resume={'new_input': None})


@pytest.mark.asyncio
async def test_readonly_query_runs_tool_then_finishes(ctx):
    await seed_bills(ctx['db'])
    graph = graph_for(fake_llm(
        tool_call_message('query_bill', {'category': '餐饮'}),
        AIMessage(content='查询完成，餐饮 1 笔 35.00 元。'),
    ), ctx['tools'])
    result = await run_to_end(graph, initial_input('run-1', '这月餐饮花了多少'),
                              {'configurable': {'thread_id': 'run-1'}})
    kinds = [e['type'] for e in result['events']]
    assert kinds == ['intent_route', 'tool_call_proposal', 'arguments_validated', 'tool_result']
    assert result['final'] == '查询完成，餐饮 1 笔 35.00 元。'
    tool_event = result['events'][-1]
    assert tool_event['payload']['tool'] == 'query_bill'
    assert tool_event['payload']['result']['ok'] is True
    data = tool_event['payload']['result']['data']
    assert isinstance(data, dict)
    assert data['count'] == 1 and data['total'] == '35.00'


@pytest.mark.asyncio
async def test_write_tool_interrupts_then_approve_executes(ctx):
    await seed_bills(ctx['db'])
    graph = graph_for(fake_llm(
        tool_call_message('create_tracking_card', {'bill_id': 2, 'category': '餐饮', 'purpose': '报销'}),
        AIMessage(content='卡片已创建。'),
    ), ctx['tools'])
    config = {'configurable': {'thread_id': 'run-2'}}
    result = await graph.ainvoke(initial_input('run-2', '给咖啡店那笔建卡'), config)
    payload = interrupted_payload(result)
    assert payload['kind'] == 'tool_approval'
    assert payload['tool'] == 'create_tracking_card'
    assert payload['args'] == {'bill_id': 2, 'category': '餐饮', 'purpose': '报销'}
    result = await run_to_end(graph, resume('approve', payload['approval_id']), config)
    assert result['final'] == '卡片已创建。'
    tool_event = [e for e in result['events'] if e['type'] == 'tool_result'][-1]
    assert tool_event['payload']['result']['ok'] is True
    assert tool_event['payload']['result']['data']['purpose'] == '报销'
    async with ctx['db'].sessions() as session:
        cards = list(await session.scalars(select(TrackingCard)))
        assert len(cards) == 1 and cards[0].bill_id == 2


@pytest.mark.asyncio
async def test_write_tool_reject_skips_execution(ctx):
    graph = graph_for(fake_llm(
        tool_call_message('create_tracking_card', {'category': '订阅', 'purpose': '待退订'}),
        AIMessage(content='好的，已取消创建。'),
    ), ctx['tools'])
    config = {'configurable': {'thread_id': 'run-3'}}
    result = await graph.ainvoke(initial_input('run-3', '建卡'), config)
    approval_id = interrupted_payload(result)['approval_id']
    result = await run_to_end(graph, resume('reject', approval_id), config)
    assert result['final'] == '好的，已取消创建。'
    tool_results = [e for e in result['events'] if e['type'] == 'tool_result']
    assert len(tool_results) == 1  # 仅用户拒绝事件，工具未被执行
    assert tool_results[0]['payload']['result']['ok'] is False
    assert tool_results[0]['payload']['result']['error']['code'] == 'USER_REJECTED'


@pytest.mark.asyncio
async def test_invalid_arguments_become_structured_validation_error(ctx):
    await seed_bills(ctx['db'])
    graph = graph_for(fake_llm(
        tool_call_message('query_bill', {'limit': 0, 'category': '餐饮'}),
        AIMessage(content='参数无效，请修正后重试。'),
    ), ctx['tools'])
    result = await run_to_end(graph, initial_input('run-4', '查餐饮'),
                              {'configurable': {'thread_id': 'run-4'}})
    tool_event = [e for e in result['events'] if e['type'] == 'tool_result'][-1]
    envelope = tool_event['payload']['result']
    assert envelope['ok'] is False
    assert envelope['error']['stage'] == 'validation'
    assert envelope['error']['retryable'] is True
    assert isinstance(result['messages'][-1], AIMessage)


@pytest.mark.asyncio
async def test_business_error_is_structured(ctx):
    await seed_bills(ctx['db'])
    graph = graph_for(fake_llm(
        tool_call_message('create_tracking_card', {'bill_id': 999, 'category': '餐饮', 'purpose': '报销'}),
        AIMessage(content='账单不存在，请先查询。'),
    ), ctx['tools'])
    config = {'configurable': {'thread_id': 'run-5'}}
    result = await graph.ainvoke(initial_input('run-5', '给不存在的账单建卡'), config)
    approval_id = interrupted_payload(result)['approval_id']
    result = await run_to_end(graph, resume('approve', approval_id), config)
    tool_event = [e for e in result['events'] if e['type'] == 'tool_result'][-1]
    envelope = tool_event['payload']['result']
    assert envelope['ok'] is False
    assert envelope['error']['code'] == 'BILL_NOT_FOUND'
    assert envelope['error']['stage'] == 'business'
    assert envelope['error']['retryable'] is False


@pytest.mark.asyncio
async def test_edited_args_resume_replaces_tool_args(ctx):
    graph = graph_for(fake_llm(
        tool_call_message('create_tracking_card', {'category': '订阅', 'purpose': '待退订', 'note': '原始备注'}),
        AIMessage(content='已按修改后参数创建。'),
    ), ctx['tools'])
    config = {'configurable': {'thread_id': 'run-6'}}
    result = await graph.ainvoke(initial_input('run-6', '建卡'), config)
    approval_id = interrupted_payload(result)['approval_id']
    result = await run_to_end(graph, resume('approve', approval_id,
                                            {'category': '餐饮', 'purpose': 'AA', 'note': '修改后的备注'}), config)
    tool_event = [e for e in result['events'] if e['type'] == 'tool_result'][-1]
    assert tool_event['payload']['result']['data']['category'] == '餐饮'
    assert tool_event['payload']['result']['data']['purpose'] == 'AA'


class LoopingFakeChatModel(GenericFakeChatModel):
    """每次都返回 query_bill 工具调用（只读、不触发审批），模拟模型停不下来的循环。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        import json

        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        call_id = f'call-loop-{len(messages)}'
        args = {'category': '餐饮'}
        yield ChatGenerationChunk(message=AIMessageChunk(
            content='',
            tool_call_chunks=[{'name': 'query_bill', 'args': json.dumps(args),
                               'id': call_id, 'index': 0}],
        ))


# 注：LangGraph 的 recursion_limit 只在单次 invoke 内计步、不跨 resume 累积；
# 检查点注入使每轮被 check_input interrupt 截断成段，图级 recursion_limit 无法再触发。
# 无限循环护栏已移到 chat.py 循环 runner 的段数护栏（见 test_chat_api 的 runner 护栏测试）。






def test_detect_skills_keyword_mapping():
    assert detect_skills('查询七月份账单') == ['skill_query']
    assert detect_skills('把转账记录做成卡片') == ['skill_query', 'skill_create']
    assert detect_skills('给张三那笔报销') == ['skill_reimburse']
    assert detect_skills('查看看板') == ['skill_board']
    assert detect_skills('你好') == []
    assert detect_skills('统计这个月支出') == ['skill_query']


def test_build_system_message_core_always_present():
    text = build_system_message([])
    assert '铁律' in text and '写操作' in text and '当前日期' in text
    assert '流程·' not in text  # 无命中时只带铁律


def test_build_system_message_injects_skills():
    text = build_system_message(['skill_query', 'skill_create'])
    assert '【流程·查询账单】' in text
    assert '【流程·创建卡片】' in text
    assert 'category_rules' in text and 'use_bill_category' in text


class RecordingFakeChatModel(GenericFakeChatModel):
    """记录每次收到的 messages，返回空回答，用于断言 system 注入内容。"""

    from pydantic import PrivateAttr

    _seen_systems: list[str] = PrivateAttr(default_factory=list)

    def __init__(self):
        super().__init__(messages=iter([]))

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """GenericFakeChatModel 的流式入口是同步 _stream（astream 会优先包装它）。"""
        for m in messages:
            if isinstance(m, SystemMessage):
                self._seen_systems.append(str(m.content))
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        yield ChatGenerationChunk(message=AIMessageChunk(content='好的。'))


@pytest.mark.asyncio
async def test_intent_injects_skill_into_system(ctx):
    llm = RecordingFakeChatModel()
    graph = graph_for(llm, ctx['tools'])
    await graph.ainvoke(initial_input('run-s1', '查询七月份账单并做成卡片'),
                        {'configurable': {'thread_id': 'run-s1'}})
    assert llm._seen_systems
    system = llm._seen_systems[0]
    assert '【流程·查询账单】' in system and '【流程·创建卡片】' in system
    assert '铁律' in system


@pytest.mark.asyncio
async def test_supervisor_routes_bill_request_to_subgraph(ctx):
    """Supervisor 路由：账单意图 → active_agent='bill' 下发账单子图（subgraph 执行）；
    闲聊 → 保持 general 走通用 agent 兜底，不经过子图。"""
    graph = graph_for(fake_llm(
        AIMessage(content='查询完成。'),
        AIMessage(content='你好呀！'),
    ), ctx['tools'])
    # 账单请求：跑通子图链路
    result = await run_to_end(graph, initial_input('sup-1', '查这个月账单'),
                              {'configurable': {'thread_id': 'sup-1'}})
    assert result['final'] == '查询完成。'
    snap = await graph.aget_state({'configurable': {'thread_id': 'sup-1'}})
    assert snap.values.get('active_agent') == 'bill'
    # 闲聊：general 域，通用 agent 直接回复
    result2 = await run_to_end(graph, initial_input('sup-2', '你好'),
                               {'configurable': {'thread_id': 'sup-2'}})
    assert result2['final'] == '你好呀！'
    snap2 = await graph.aget_state({'configurable': {'thread_id': 'sup-2'}})
    assert snap2.values.get('active_agent') == 'general'


@pytest.mark.asyncio
async def test_supervisor_append_re_routes_into_bill_subgraph(ctx):
    """执行中追加：check_input 注入新输入后回 intent 重新路由——
    账单任务中途追加账单补充条件，仍进入账单子图继续，不重跑已完成步骤。"""

    graph = graph_for(fake_llm(
        AIMessage(content='已查询。'),
        AIMessage(content='已在原范围二次筛选。'),
    ), ctx['tools'])
    config = {'configurable': {'thread_id': 'sup-3'}}
    # 第一段：账单查询完成，停在输入检查点
    result = await graph.ainvoke(initial_input('sup-3', '查8月账单'), config)
    assert any(isinstance(i.value, dict) and i.value.get('kind') == 'input_checkpoint'
               for i in result.get('__interrupt__', []))
    # 追加补充条件 → intent 重新路由 → 仍进账单子图
    result = await run_to_end(graph, Command(resume={'new_input': '只要餐饮'}), config)
    assert result['final'] == '已在原范围二次筛选。'
    snap = await graph.aget_state(config)
    texts = [str(getattr(m, 'content', '')) for m in snap.values['messages']]
    assert sum(1 for t in texts if t == '只要餐饮') == 1
    assert snap.values.get('active_agent') == 'bill'


@pytest.mark.asyncio
async def test_supervisor_routes_search_request_to_subgraph(ctx):
    """Supervisor 路由：搜索意图 → active_agent='search' 下发搜索子图；
    子图内模型可继续调 web_search（工具全集绑定），完成搜索后 END 回主图。"""
    graph = graph_for(fake_llm(
        AIMessage(content='杭州最近晴到多云。'),
        AIMessage(content='你好呀！'),
    ), ctx['tools'])
    # 搜索请求：跑通搜索子图链路，system 注入搜索流程
    result = await run_to_end(graph, initial_input('sup-4', '查一下杭州天气'),
                              {'configurable': {'thread_id': 'sup-4'}})
    assert result['final'] == '杭州最近晴到多云。'
    snap = await graph.aget_state({'configurable': {'thread_id': 'sup-4'}})
    assert snap.values.get('active_agent') == 'search'
    systems = [str(m.content) for m in snap.values['messages']
               if isinstance(m, SystemMessage)]
    assert any('【流程·搜索】' in s for s in systems)


@pytest.mark.asyncio
async def test_supervisor_routes_schedule_request_to_subgraph(ctx):
    """Supervisor 路由：日程意图 → active_agent='schedule' 下发日程子图；
    子图内模型可继续调日程工具（list_events 等），完成返回主图。"""
    graph = graph_for(fake_llm(
        AIMessage(content='已按日程完成处理。'),
        AIMessage(content='你好呀！'),
    ), ctx['tools'])
    result = await run_to_end(graph, initial_input('sup-5', '查一下这周的日程安排'),
                              {'configurable': {'thread_id': 'sup-5'}})
    assert result['final'] == '已按日程完成处理。'
    snap = await graph.aget_state({'configurable': {'thread_id': 'sup-5'}})
    assert snap.values.get('active_agent') == 'schedule'
    systems = [str(m.content) for m in snap.values['messages']
               if isinstance(m, SystemMessage)]
    assert any('【流程·日程】' in s for s in systems)


@pytest.mark.asyncio
async def test_schedule_write_goes_through_hitl_approval(ctx):
    """日程写工具（create_event）必须经 HITL 审批：子图内 interrupt tool_approval，
    resume approve 后才执行建卡；reject 则不执行。"""
    graph = graph_for(fake_llm(
        tool_call_message('create_event', {'title': '测试会议', 'start_at': '2026-10-03T15:00'}, 'call-9'),
        AIMessage(content='已创建。'),
    ), ctx['tools'])
    r = await run_to_end(graph, initial_input('sup-9', '明天安排一个测试会议'),
                         {'configurable': {'thread_id': 'sup-9'}})
    ap = interrupted_payload(r)
    assert ap['kind'] == 'tool_approval' and ap['tool'] == 'create_event'
    assert ap['approval_id'] == 'apr-call-9'
    snap = await graph.aget_state({'configurable': {'thread_id': 'sup-9'}})
    assert snap.values.get('active_agent') == 'schedule'
    # resume approve → 建卡完成，final 产出
    r2 = await run_to_end(graph, resume('approve', ap['approval_id']),
                          {'configurable': {'thread_id': 'sup-9'}})
    assert r2['final'] == '已创建。'
    assert r2.get('__interrupt__') is None or all(
        not (isinstance(i.value, dict) and i.value.get('kind') == 'tool_approval')
        for i in r2['__interrupt__'])


@pytest.mark.asyncio
async def test_detect_agent_four_way_routing(ctx):
    """领域路由单测：账单域优先 → 日程域 → 搜索域 → 通用兜底。"""
    from app.agent.graph import detect_agent
    assert detect_agent(detect_skills('查8月账单')) == 'bill'          # 账单词命中，bill 优先
    assert detect_agent(detect_skills('把转账做成卡片')) == 'bill'
    assert detect_agent(detect_skills('明天安排会议')) == 'schedule'    # 仅日程词 → schedule
    assert detect_agent(detect_skills('查一下明天的日程')) == 'schedule'
    assert detect_agent(detect_skills('查一下杭州天气')) == 'search'    # 仅搜索词 → search
    assert detect_agent(detect_skills('搜索最新旅游攻略')) == 'search'
    assert detect_agent(detect_skills('你好')) == 'general'             # 未命中 → general


@pytest.mark.asyncio
async def test_intent_keeps_core_only_for_unrelated_chat(ctx):
    llm = RecordingFakeChatModel()
    graph = graph_for(llm, ctx['tools'])
    await graph.ainvoke(initial_input('run-s2', '你好'), {'configurable': {'thread_id': 'run-s2'}})
    system = llm._seen_systems[0]
    assert '【流程·' not in system
    assert '铁律' in system
