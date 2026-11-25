import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.personal import router
from app.auth import AuthMiddleware, sign_token
from app.data.models import User
from app.services import personal_goals, personal_tasks

BASE = '/api/personal'
SECRET = 'personal-api-test'


@pytest.fixture
async def client(db):
    async with db.sessions.begin() as session:
        session.add_all([
            User(id=1, username='personal-owner', password_hash='unused'),
            User(id=2, username='another-owner', password_hash='unused'),
        ])
    app = FastAPI()
    app.add_middleware(AuthMiddleware, secret=SECRET)
    app.include_router(router)
    app.state.db = db
    auth = {'Authorization': f'Bearer {sign_token(1, SECRET)}'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=auth) as client:
        yield client


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


async def test_task_lifecycle_reminders_and_ownership(client):
    task = checked(await client.post(f'{BASE}/tasks', json={'title': '课程作业'}), 201)
    assert task['board_status'] == 'inbox'
    path = f"{BASE}/tasks/{task['id']}"
    other = {'Authorization': f'Bearer {sign_token(2, SECRET)}'}
    assert (await client.get(path, headers=other)).status_code == 404
    assert checked(await client.get(f'{BASE}/tasks', headers=other)) == []

    task = checked(await client.patch(path, json={
        'version': task['version'], 'board_status': 'todo',
        'due_at': '2000-01-02T18:00:00+08:00', 'reminder_at': '2000-01-02T08:00:00+08:00',
        'is_waiting': True, 'follow_up_at': '2099-01-03T09:00:00+08:00',
    }))
    assert task['overdue'] and task['reminder_at'] and task['follow_up_at']
    assert len(checked(await client.get(f'{BASE}/tasks', params={'recent_due': True}))) == 1
    task = checked(await client.post(f'{path}/check-items', json={
        'version': task['version'], 'label': '上传实验结果',
    }), 201)
    item_id = task['check_items'][0]['id']
    task = checked(await client.patch(f'{path}/check-items/{item_id}', json={
        'version': task['version'], 'checked': True,
    }))
    assert task['checked_count'] == 1
    reminders = checked(await client.get(f'{BASE}/reminders'))
    assert len(reminders) == 1 and reminders[0]['kind'] == 'start'
    assert checked(await client.get(f'{BASE}/reminders')) == reminders
    reminder_id = reminders[0]['id']
    assert (await client.post(f'{BASE}/reminders/{reminder_id}/read')).status_code == 204
    assert checked(await client.get(f'{BASE}/reminders')) == []

    stale = await client.patch(path, json={'version': task['version'] - 1, 'board_status': 'done'})
    assert stale.status_code == 409
    task = checked(await client.patch(path, json={'version': task['version'], 'board_status': 'done'}))
    assert task['completed_at'] and not task['is_waiting'] and not task['overdue']
    task = checked(await client.patch(path, json={'version': task['version'], 'board_status': 'todo'}))
    assert task['completed_at'] is None and task['reminder_at'] is None
    task = checked(await client.post(f'{path}/archive', json={'version': task['version']}))
    assert checked(await client.get(f'{BASE}/tasks')) == []
    assert checked(await client.get(f'{BASE}/tasks', params={'archived': True}))[0]['id'] == task['id']
    assert checked(await client.post(f'{path}/restore', json={'version': task['version']}))['archived_at'] is None


async def test_goal_progress_retry_modes_and_history(client):
    goal = checked(await client.post(f'{BASE}/goals', json={
        'title': '年度跑步', 'progress_mode': 'metric', 'unit': '公里', 'target_value': 100,
    }), 201)
    path = f"{BASE}/goals/{goal['id']}"
    first = checked(await client.post(f'{path}/progress', json={
        'version': goal['version'], 'kind': 'metric', 'value': 68, 'request_key': 'initial-run',
    }))
    payload = {'version': first['goal']['version'], 'kind': 'metric', 'value': 5, 'request_key': 'weekend-run'}
    step = checked(await client.post(f'{path}/progress', json=payload))
    assert Decimal(step['goal']['progress_percent']) == 73
    assert checked(await client.post(f'{path}/progress', json=payload)) == step
    goal = checked(await client.patch(path, json={
        'version': step['goal']['version'], 'target_value': 110, 'progress_style': 'plant',
    }))
    history = checked(await client.get(f'{path}/progress'))
    assert history[0]['entry_type'] == 'target_change'
    assert Decimal(goal['progress_percent']) == Decimal('66.36')
    assert (await client.patch(path, json={'version': goal['version'], 'progress_mode': 'manual'})).status_code == 409
    other = {'Authorization': f'Bearer {sign_token(2, SECRET)}'}
    assert (await client.post(f'{BASE}/tasks', headers=other, json={
        'title': 'invalid link', 'goal_id': goal['id'],
    })).status_code == 404

    goal = checked(await client.post(f'{BASE}/goals', json={
        'title': '作品集', 'progress_mode': 'milestone', 'milestones': ['选题', '初稿', '发布'],
    }), 201)
    path = f"{BASE}/goals/{goal['id']}/progress"
    for key, completed, expected in [('draft-done', True, '33.33'), ('reopened', False, '0')]:
        result = checked(await client.post(path, json={
            'version': goal['version'], 'kind': 'milestone', 'request_key': key,
            'milestone_id': goal['milestones'][1]['id'], 'completed': completed,
        }))
        goal = result['goal']
        assert Decimal(goal['progress_percent']) == Decimal(expected)


async def test_period_snapshot_ai_selection_and_adoption(client, monkeypatch):
    clock = datetime(2026, 11, 23, 0, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(personal_tasks, 'now_utc', lambda: clock.astimezone(UTC))
    monkeypatch.setattr(personal_goals, 'now_utc', lambda: clock.astimezone(UTC))
    task = checked(await client.post(f'{BASE}/tasks', json={
        'title': '课程作业', 'due_at': '2026-11-22T18:00:00+08:00',
    }), 201)
    path = f"{BASE}/tasks/{task['id']}"
    for status in ['done', 'todo', 'done']:
        task = checked(await client.patch(path, json={
            'version': task['version'], 'board_status': status, 'due_at': '2026-11-24T18:00:00+08:00',
        }))
    goal = checked(await client.post(f'{BASE}/goals', json={
        'title': '独立目标', 'progress_mode': 'manual',
    }), 201)

    period = {'kind': 'week', 'day': '2026-11-25'}
    previous = checked(await client.get(f'{BASE}/reviews', params={'kind': 'week', 'day': '2026-11-22'}))
    assert previous['source_snapshot']['completed_tasks'] == 0
    review = checked(await client.put(f'{BASE}/reviews', params=period, json={
        'version': 0, 'user_notes': '只保留在复盘草稿',
    }))
    assert (review['period_start'], review['period_end']) == ('2026-11-23', '2026-11-30')
    assert review['source_snapshot']['completed_tasks'] == review['source_snapshot']['delayed_tasks'] == 1
    assert review['source_snapshot']['goals'][0]['id'] == goal['id']

    class Model:
        async def ainvoke(self, messages):
            evidence = json.loads(messages[1][1])
            assert set(evidence) == {f"task:{task['id']}"}
            return SimpleNamespace(content=json.dumps({
                'summary': '完成了课程作业',
                'suggestions': [{'text': '给作业设置开工提醒', 'references': list(evidence)}],
            }))

    client._transport.app.state.personal_review_model = Model()
    review_path = f"{BASE}/reviews/{review['id']}"
    draft = checked(await client.post(f'{review_path}/generate', json={
        'version': review['version'], 'task_ids': [task['id']],
    }))
    assert draft['user_notes'] == '只保留在复盘草稿'
    key = draft['ai_draft']['suggestions'][0]['key']
    adopt_path = f'{review_path}/suggestions/{key}/adopt'
    new_task = checked(await client.post(adopt_path))
    assert new_task['board_status'] == 'inbox'
    assert checked(await client.post(adopt_path))['id'] == new_task['id']

    monthly = checked(await client.put(f'{BASE}/reviews', params={'kind': 'month', 'day': '2026-11-25'}, json={
        'version': 0, 'user_notes': '', 'selected_week_ids': [review['id']],
    }))
    assert monthly['source_snapshot']['completed_tasks'] == 1
    assert monthly['source_snapshot']['week_excerpts'][0]['id'] == review['id']
