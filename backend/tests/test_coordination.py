"""Real PostgreSQL tests; independent coordinators represent API replicas."""
import asyncio
import sys

import pytest
from fastapi import HTTPException

from app.services.coordination import Coordinator


async def test_lock_excludes_a_separate_python_process(db):
    # The child has its own interpreter, engine, pool and memory.
    child_code = '''
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from types import SimpleNamespace
from app.services.coordination import Coordinator
async def main():
    engine = create_async_engine(sys.argv[1], poolclass=NullPool)
    lease = await Coordinator(SimpleNamespace(engine=engine)).acquire('u1:process')
    print('locked' if lease else 'busy', flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    if lease:
        await lease.close()
    await engine.dispose()
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-c', child_code, db.url,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), 10)
        assert ready.strip() == b'locked'
        assert await Coordinator(db).acquire('u1:process') is None
    finally:
        process.stdin.write(b'release\n')
        await process.stdin.drain()
        await asyncio.wait_for(process.wait(), 10)
    assert process.returncode == 0
    lease = await Coordinator(db).acquire('u1:process')
    assert lease is not None
    await lease.close()


async def test_two_api_states_resume_only_one_executor(db):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.api.chat import resume
    from app.schemas import ResumeRequest

    entered, finish = asyncio.Event(), asyncio.Event()

    class Graph:
        calls = 0

        async def aget_state(self, config):
            value = {'kind': 'tool_approval', 'approval_id': 'a',
                     'created_at': datetime.now(timezone.utc).isoformat()}
            return SimpleNamespace(values={'messages': [], 'events': []}, next=('approve',),
                                   tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value=value)])])

        async def astream(self, *args, **kwargs):
            self.calls += 1
            entered.set()
            await finish.wait()
            yield {'agent': {'final': 'done'}}

    graph = Graph()

    def request():
        state = SimpleNamespace(
            coordinator=Coordinator(db), graph=graph, checkpointer=None,
            settings=SimpleNamespace(max_steps=8, run_timeout_seconds=15,
                                     max_tool_calls_per_run=20, approval_ttl_seconds=600))
        return SimpleNamespace(app=SimpleNamespace(state=state),
                               state=SimpleNamespace(user_id=1, request_id=None))

    payload = ResumeRequest(approval_id='a', decision='approve')
    response = await resume('u1:s', payload, request())

    async def consume():
        return [item async for item in response.body_iterator]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises(HTTPException) as exc:
            await resume('u1:s', payload, request())
        assert exc.value.status_code == 409
        assert graph.calls == 1
    finally:
        finish.set()
        await task
    lease = await Coordinator(db).acquire('u1:s')
    assert lease is not None
    await lease.close()


async def test_conflicting_decisions_race_has_one_winner(db):
    async def decide(value):
        try:
            await Coordinator(db).decision('u1:s', 'a', {'decision': value})
            return 'accepted'
        except HTTPException as exc:
            return exc.status_code

    results = await asyncio.gather(decide('approve'), decide('reject'))
    assert results.count('accepted') == 1
    assert results.count(409) == 1


async def test_session_exclusion_and_release(db):
    first, second = Coordinator(db), Coordinator(db)
    lease = await first.acquire('u1:shared')
    assert lease is not None
    try:
        assert await second.acquire('u1:shared') is None
        other = await second.acquire('u2:shared')
        assert other is not None
        await other.close()
    finally:
        await lease.close()
    recovered = await second.acquire('u1:shared')
    assert recovered is not None
    await recovered.close()


async def test_durable_inbox_and_checkpoint_dedup(db):
    first = Coordinator(db)
    mid = await first.enqueue('u1:s', '追加任务')
    restarted = Coordinator(db)
    assert await restarted.pending('u1:s', []) == [(mid, '追加任务')]
    assert await restarted.pending('u1:s', [mid]) == []
    assert await restarted.pending('u2:s', []) == []


async def test_immutable_decision_concurrent_retries(db):
    first, second = Coordinator(db), Coordinator(db)
    decision = {'decision': 'approve', 'approval_id': 'a'}
    await asyncio.gather(first.decision('u1:s', 'a', decision),
                         second.decision('u1:s', 'a', decision))
    with pytest.raises(HTTPException) as exc:
        await second.decision('u1:s', 'a', {'decision': 'reject', 'approval_id': 'a'})
    assert exc.value.status_code == 409


async def test_lost_connection_releases_database_lock(db):
    first, second = Coordinator(db), Coordinator(db)
    lease = await first.acquire('u1:crash')
    await lease.connection.invalidate()
    with pytest.raises(RuntimeError):
        await lease.check()
    await lease.connection.close()
    recovered = await second.acquire('u1:crash')
    assert recovered is not None
    await recovered.close()
