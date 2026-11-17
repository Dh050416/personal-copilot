"""PostgreSQL coordination for API replicas; no process-local correctness state."""
import asyncio
import hashlib
import json
from contextlib import suppress
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text


class SessionLease:
    def __init__(self, connection, key):
        self.connection = connection
        self.key = key

    async def check(self):
        # Never reconnect a lost session: that would silently lose its advisory lock.
        if self.connection.invalidated or self.connection.closed:
            raise RuntimeError('会话锁连接已失效')
        await self.connection.execute(text('SELECT 1'))
        await self.connection.commit()

    async def close(self):
        try:
            await self.connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': self.key})
            await self.connection.commit()
        except BaseException:
            await self.connection.invalidate()
            raise
        finally:
            await self.connection.close()


class Coordinator:
    def __init__(self, db):
        self.db = db

    async def acquire(self, thread_id):
        key = int.from_bytes(hashlib.sha256(thread_id.encode()).digest()[:8], 'big', signed=True)
        conn = await self.db.engine.connect()
        try:
            acquired = await conn.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key})
            await conn.commit()
            if acquired:
                return SessionLease(conn, key)
            await conn.close()
            return None
        except BaseException:
            await conn.invalidate()
            await conn.close()
            raise

    async def enqueue(self, thread_id, message):
        message_id = uuid4().hex
        async with self.db.engine.begin() as conn:
            await conn.execute(text('INSERT INTO agent_inputs (id, thread_id, content) '
                                    'VALUES (:id, :thread, :content)'),
                               {'id': message_id, 'thread': thread_id, 'content': message})
        return message_id

    async def pending(self, thread_id, applied):
        async with self.db.engine.connect() as conn:
            rows = (await conn.execute(text('SELECT id, content FROM agent_inputs '
                                           'WHERE thread_id=:thread ORDER BY sequence'),
                                       {'thread': thread_id})).all()
        return [(row.id, row.content) for row in rows if row.id not in applied]

    async def decision(self, thread_id, approval_id, decision):
        encoded = json.dumps(decision, sort_keys=True, ensure_ascii=False)
        async with self.db.engine.begin() as conn:
            await conn.execute(text('INSERT INTO agent_decisions '
                                    '(thread_id, approval_id, decision) VALUES (:thread, :approval, :decision) '
                                    'ON CONFLICT (thread_id, approval_id) DO NOTHING'),
                               {'thread': thread_id, 'approval': approval_id, 'decision': encoded})
            saved = await conn.scalar(text('SELECT decision FROM agent_decisions '
                                           'WHERE thread_id=:thread AND approval_id=:approval'),
                                      {'thread': thread_id, 'approval': approval_id})
        if saved != encoded:
            raise HTTPException(409, '该审批已接受不同的决定')


async def coordinated_response(response, lease):
    """Hold execution ownership until SSE iterator has fully stopped, including disconnects."""
    original = response.body_iterator

    async def body():
        owner = asyncio.current_task()
        lost = asyncio.Event()

        async def monitor():
            try:
                while True:
                    await asyncio.sleep(1)
                    await lease.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                lost.set()
                owner.cancel()

        watcher = asyncio.create_task(monitor())
        try:
            async for item in original:
                if lost.is_set():
                    raise RuntimeError('会话执行锁已丢失')
                yield item
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            try:
                await original.aclose()
            finally:
                await asyncio.shield(lease.close())

    response.body_iterator = body()
    return response
