"""日历手动录入的直连 REST 写接口回归。

日历表单是确定性输入，不应绕 /api/chat 走 Agent + HITL。这里用独立 mini app
只挂 events_router（没有 chat 路由、没有 graph、没有审批节点），既能跑通就证明
这条链路与 Agent 完全解耦；同时锁死与工具层共用的 service 行为：时间规范化、
参数校验、幂等、用户隔离、ToolError -> HTTP 404 的映射。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import router as events_router
from app.data.models import ScheduleEvent
from tests.conftest import TEST_USER_ID


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(events_router)
    app.state.db = db

    @app.middleware('http')
    async def inject_user(request, call_next):
        request.state.user_id = TEST_USER_ID
        return await call_next(request)

    return TestClient(app)


def new_event(client, **overrides):
    body = {'title': '产品评审', 'start_at': '2026-11-11T09:00',
            'end_at': '2026-11-11T10:00', 'location': '会议室 A', 'note': '带笔记本'}
    body.update(overrides)
    return client.post('/api/events', json=body)


def test_create_returns_event_and_is_queryable(client):
    resp = new_event(client)
    assert resp.status_code == 200
    view = resp.json()
    assert view['title'] == '产品评审'
    assert view['start_at'] == '2026-11-11T09:00:00'  # schema 归一化补秒
    assert view['end_at'] == '2026-11-11T10:00:00'
    assert view['location'] == '会议室 A' and view['note'] == '带笔记本'
    assert view['status'] == 'active'

    listed = client.get('/api/events', params={'status': 'all'}).json()['items']
    assert [e['id'] for e in listed] == [view['id']]
    # 直连端点返回的是普通 JSON 视图，不是 SSE / 审批事件
    assert resp.headers['content-type'].startswith('application/json')


def test_create_validates_payload(client):
    assert new_event(client, title='').status_code == 422          # 标题必填
    assert new_event(client, start_at='昨天下午').status_code == 422  # 时间格式
    assert new_event(client, end_at='2026-11-11T08:00').status_code == 422  # 早于开始
    assert client.post('/api/events', json={'title': 'x', 'start_at': '2026-11-11T09:00',
                                            'bogus': 1}).status_code == 422  # extra=forbid
    assert client.get('/api/events', params={'status': 'all'}).json()['items'] == []


def test_complete_is_idempotent(client):
    event_id = new_event(client).json()['id']
    first = client.post(f'/api/events/{event_id}/complete')
    assert first.status_code == 200 and first.json()['status'] == 'done'
    second = client.post(f'/api/events/{event_id}/complete')
    assert second.status_code == 200 and second.json()['status'] == 'done'
    done = client.get('/api/events', params={'status': 'done'}).json()['items']
    assert len(done) == 1 and done[0]['id'] == event_id


def test_update_changes_start_at(client):
    event_id = new_event(client).json()['id']
    resp = client.patch(f'/api/events/{event_id}',
                        json={'event_id': event_id, 'start_at': '2026-11-12T14:30'})
    assert resp.status_code == 200
    assert resp.json()['start_at'] == '2026-11-12T14:30:00'
    assert resp.json()['title'] == '产品评审'  # 未提供的字段保持不变
    assert client.get('/api/events', params={'status': 'all'}).json()['items'][0]['start_at'] \
        == '2026-11-12T14:30:00'


def test_update_rejects_mismatched_or_empty_path(client):
    event_id = new_event(client).json()['id']
    mismatch = client.patch(f'/api/events/{event_id}',
                            json={'event_id': event_id + 999, 'start_at': '2026-11-12T14:30'})
    assert mismatch.status_code == 400
    no_change = client.patch(f'/api/events/{event_id}', json={'event_id': event_id})
    assert no_change.status_code == 422  # 至少一个待修改字段
    assert client.get('/api/events', params={'status': 'all'}).json()['items'][0]['start_at'] \
        == '2026-11-11T09:00:00'


@pytest.mark.parametrize('method,body', [
    ('complete', None),
    ('update', {'event_id': 999999, 'start_at': '2026-11-12T14:30'}),
])
def test_missing_event_maps_toolerror_to_404(client, method, body):
    if method == 'complete':
        resp = client.post('/api/events/999999/complete')
    else:
        resp = client.patch('/api/events/999999', json=body)
    assert resp.status_code == 404
    assert '日程不存在' in resp.json()['detail']


def test_real_app_writes_without_agent_or_approval(db, auth_headers):
    """真实 app（含 AuthMiddleware / RequestIdMiddleware）上验证整条解耦链路：
    POST /api/events 直接落库返回普通 JSON，既不进 /api/chat，也不产生审批事件。"""
    from app.main import app as real_app

    previous = getattr(real_app.state, 'db', None)
    real_app.state.db = db
    try:
        http = TestClient(real_app, headers=auth_headers)
        resp = http.post('/api/events', json={
            'title': '手动录入', 'start_at': '2026-11-11T09:00'})
        assert resp.status_code == 200
        assert resp.headers['content-type'].startswith('application/json')
        assert resp.json()['title'] == '手动录入'
        assert 'approval' not in resp.text.lower()
        assert resp.headers.get('X-Request-ID')  # A+C 的 rid 同样覆盖直连写操作
        assert [e['title'] for e in
                http.get('/api/events', params={'status': 'all'}).json()['items']] == ['手动录入']
    finally:
        if previous is None:
            del real_app.state.db
        else:
            real_app.state.db = previous


def test_other_users_event_is_invisible_and_unwritable(client, db):
    """service 层的 user_id 隔离：别人日程查不到，complete/update 都是 404。"""
    async def seed():
        async with db.sessions() as session:
            async with session.begin():
                session.add(ScheduleEvent(
                    user_id=TEST_USER_ID + 1, title='别人的日程',
                    start_at='2026-11-11T09:00:00', end_at='', location='', note='',
                    invocation_id='other-user-event'))
                await session.flush()
                return None
    import asyncio
    asyncio.run(seed())

    assert client.get('/api/events', params={'status': 'all'}).json()['items'] == []
    assert client.post('/api/events/1/complete').status_code == 404
    assert client.patch('/api/events/1',
                        json={'event_id': 1, 'title': '篡改'}).status_code == 404
