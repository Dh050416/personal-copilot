"""Exercise actual Alembic history, not ORM create_all, against an empty database."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings


async def test_empty_database_upgrade_and_repeat():
    # Never clear the configured business/test database. Create and drop only a
    # fresh, randomly named database owned by this test.
    target = 'copilot_migration_smoke_' + uuid4().hex[:12]
    base_url = make_url(Settings().test_database_url)
    admin = create_async_engine(base_url, poolclass=NullPool, isolation_level='AUTOCOMMIT')
    target_url = base_url.set(database=target)
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{target}"'))
            created = True
        env = {**os.environ, 'DATABASE_URL': target_url.render_as_string(hide_password=False)}
        for _ in range(2):
            result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                                    cwd=Path(__file__).resolve().parents[1], env=env,
                                    capture_output=True, text=True, timeout=90)
            assert result.returncode == 0, result.stdout + result.stderr
        fresh = create_async_engine(target_url, poolclass=NullPool)
        try:
            async with fresh.connect() as connection:
                assert (await connection.execute(text('SELECT version_num FROM alembic_version'))).scalar_one() == '52da146a0310'
                for table in ('knowledge_documents', 'knowledge_chunks', 'agent_inputs', 'agent_decisions',
                              'bill_import_jobs', 'bill_import_rows', 'bill_refund_links',
                              'personal_tasks', 'personal_task_check_items', 'personal_task_events',
                              'personal_task_reminders', 'personal_goals', 'personal_goal_milestones',
                              'personal_goal_progress_entries', 'personal_reviews'):
                    assert (await connection.execute(text('SELECT to_regclass(:name)'), {'name': table})).scalar_one()
                columns = set((await connection.execute(text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='knowledge_documents'"))).scalars())
                assert {'source_bytes', 'embedding_model', 'ingest_owner', 'retry_at'} <= columns
        finally:
            await fresh.dispose()
    finally:
        if created:
            async with admin.connect() as connection:
                await connection.execute(text(f'DROP DATABASE "{target}"'))
        await admin.dispose()
