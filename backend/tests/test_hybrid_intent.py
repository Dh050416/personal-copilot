import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select

from app.agent.graph import initial_input, make_graph
from app.agent.intent import Intent, classify_intent
from app.data.models import PersonalReview, PersonalTask, User
from app.personal_schemas import GoalCreate, MetricProgress, TaskCreate
from app.services.personal_goals import create_goal, record_goal_progress
from app.services.personal_tasks import create_task
from app.tools import OUTPUT_SCHEMAS, WRITE_TOOLS, build_tools
from app.tools.context import _IDEMPOTENCY_KEY
from tests.conftest import TEST_USER_ID
from tests.test_graph_routing import fake_llm, interrupted_payload, resume, run_to_end, tool_call_message


class RouterModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def ainvoke(self, messages):
        self.requests.append(json.loads(messages[-1].content))
        response = next(self.responses)
        return AIMessage(content=json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else response)


def decision(domain, action, **hints):
    return {'domain': domain, 'action': action, 'hints': hints}


@pytest.fixture
async def personal_db(db):
    async with db.sessions.begin() as session:
        session.add(User(id=TEST_USER_ID, username='hybrid-test', password_hash='unused'))
    return db


def hybrid(db, router, *messages):
    return make_graph(
        fake_llm(*messages), build_tools(db), WRITE_TOOLS, MemorySaver(),
        intent_model=router, output_schemas=OUTPUT_SCHEMAS,
    )


async def test_rule_shortcut_and_semantic_fallback():
    model = RouterModel(decision('personal', 'update_personal_task', entity='作业'), '{"domain":"bill","action":"save_review"}')
    route, source = await classify_intent(model, '帮我做上周复盘', [], None, [])
    assert (route.domain, route.action, route.hints.period, source) == ('personal', 'draft_review', '上周', 'rule')
    assert model.requests == []
    route, source = await classify_intent(model, '作业搞定了', [], route.model_dump(), ['skill_board'])
    assert (route.domain, route.action, source) == ('personal', 'update_personal_task', 'model')
    assert model.requests[0]['previous_intent']['action'] == 'draft_review'
    assert model.requests[0]['keyword_signals'] == ['skill_board']
    route, source = await classify_intent(model, '帮我处理一下', [], None, [])
    assert route == Intent(domain='general', action='clarify') and source == 'fallback'


async def test_create_personal_task_requires_approval_and_replays(personal_db):
    args = {'title': '提交报销材料', 'due_at': '2026-11-27T18:00:00+08:00'}
    model = RouterModel(decision('personal', 'create_personal_task', entity=args['title']))
    graph = hybrid(personal_db, model,
                   tool_call_message('create_personal_task', args, 'create-task'),
                   AIMessage(content='已经放进收件箱。'))
    config = {'configurable': {'thread_id': 'create-personal'}}
    result = await graph.ainvoke(initial_input('create-personal', '给我建个待办，周五18点前提交报销材料'), config)
    proposal = interrupted_payload(result)
    assert proposal['tool'] == 'create_personal_task'
    assert model.requests[0]['keyword_signals'] == ['skill_create', 'skill_reimburse']
    async with personal_db.sessions() as session:
        assert await session.scalar(select(func.count(PersonalTask.id))) == 0

    result = await run_to_end(graph, resume('approve', proposal['approval_id']), config)
    assert result['active_agent'] == 'personal'
    assert result['intent'] == decision('personal', 'create_personal_task', entity=args['title'])
    assert result['events'][0]['type'] == 'intent_classified'
    assert result['final'] == '已经放进收件箱。'

    tool = next(tool for tool in build_tools(personal_db) if tool.name == 'create_personal_task')
    token = _IDEMPOTENCY_KEY.set(proposal['approval_id'])
    try:
        replay = await tool.arun(args, tool_call_id='replay')
    finally:
        _IDEMPOTENCY_KEY.reset(token)
    assert replay.artifact.title == args['title']
    async with personal_db.sessions() as session:
        assert await session.scalar(select(func.count(PersonalTask.id))) == 1


async def test_spoken_goal_progress_uses_lookup_and_preview(personal_db):
    async with personal_db.sessions.begin() as session:
        goal = await create_goal(session, TEST_USER_ID, GoalCreate(
            title='年度跑步计划', progress_mode='metric', target_value=100, unit='公里',
        ))
        goal, _ = await record_goal_progress(session, TEST_USER_ID, goal.id, MetricProgress(
            version=goal.version, kind='metric', request_key='seed-run', value=68,
        ))
    router = RouterModel(decision('personal', 'record_goal_progress', entity='跑步', amount=5, unit='公里'))
    graph = hybrid(
        personal_db, router,
        tool_call_message('find_personal_goals', {'query': '跑步'}, 'find-running'),
        tool_call_message('record_goal_progress', {
            'goal_id': goal.id, 'version': goal.version, 'value': 5, 'unit': '公里',
        }, 'record-running'),
        AIMessage(content='本次已记录。'),
    )
    config = {'configurable': {'thread_id': 'progress-personal'}}
    result = await graph.ainvoke(initial_input('progress-personal', '今天跑了5公里'), config)
    payload = interrupted_payload(result)
    assert payload['tool'] == 'record_goal_progress'
    assert '68% 更新到 73%' in payload['message']
    result = await run_to_end(graph, resume('approve', payload['approval_id']), config)
    data = next(event['payload']['result']['data'] for event in result['events']
                if event['type'] == 'tool_result' and event['payload']['tool'] == 'record_goal_progress')
    assert data['goal']['progress_percent'] == '73.00'
    assert data['entry']['before_data']['progress_percent'] == '68.00'


async def test_spoken_task_completion_resolves_database_record(personal_db):
    async with personal_db.sessions.begin() as session:
        task = await create_task(session, TEST_USER_ID, TaskCreate(title='数学作业'))
    router = RouterModel(decision('personal', 'update_personal_task', entity='作业'))
    graph = hybrid(
        personal_db, router,
        tool_call_message('find_personal_tasks', {'query': '作业'}, 'find-assignment'),
        tool_call_message('update_personal_task', {
            'task_id': task.id, 'version': task.version, 'board_status': 'done',
        }, 'complete-assignment'),
        AIMessage(content='数学作业已完成。'),
    )
    config = {'configurable': {'thread_id': 'assignment'}}
    result = await graph.ainvoke(initial_input('assignment', '作业完成了'), config)
    proposal = interrupted_payload(result)
    assert '数学作业' in proposal['message']
    result = await run_to_end(graph, resume('approve', proposal['approval_id']), config)
    assert result['final'] == '数学作业已完成。'
    assert result['events'][-1]['payload']['result']['ok'] is True, result['events'][-1]
    async with personal_db.sessions() as session:
        stored = await session.get(PersonalTask, task.id)
        assert stored.board_status == 'done' and stored.completed_at is not None


async def test_review_loop_reads_draft_saves_only_after_followup(personal_db):
    day = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    period = {'kind': 'week', 'day': day}
    save_args = {**period, 'version': 0, 'final_text': '本周暂无任务或目标变化。'}
    router = RouterModel(decision('personal', 'save_review', period='本周'))
    graph = hybrid(
        personal_db, router,
        tool_call_message('save_personal_review', save_args, 'wrong-draft-write'),
        tool_call_message('get_personal_review_context', period, 'draft-context'),
        AIMessage(content='本周暂无任务或目标变化。'),
        tool_call_message('get_personal_review_context', period, 'save-context'),
        tool_call_message('save_personal_review', save_args, 'save-review'),
        AIMessage(content='本周复盘已保存。'),
    )
    config = {'configurable': {'thread_id': 'personal-review'}}
    result = await run_to_end(graph, initial_input('personal-review', '帮我做本周复盘'), config)
    assert router.requests == []
    assert result['intent_source'] == 'rule'
    assert any(event['payload'].get('result', {}).get('error', {}).get('code') == 'TOOL_NOT_ALLOWED'
               for event in result['events'] if event['type'] == 'tool_result'
               and event['payload']['result']['error'])
    async with personal_db.sessions() as session:
        assert await session.scalar(select(func.count(PersonalReview.id))) == 0

    result = await graph.ainvoke({'messages': [*result['messages'], HumanMessage(content='保存这份复盘')]}, config)
    proposal = interrupted_payload(result)
    assert proposal['tool'] == 'save_personal_review'
    assert router.requests[0]['previous_intent']['action'] == 'draft_review'
    result = await run_to_end(graph, resume('approve', proposal['approval_id']), config)
    assert result['final'] == '本周复盘已保存。'
    async with personal_db.sessions() as session:
        review = await session.scalar(select(PersonalReview))
        assert review.status == 'saved' and review.final_text == save_args['final_text']
