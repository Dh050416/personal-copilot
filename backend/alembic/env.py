"""Alembic 迁移环境配置。

- 从 app.config.settings 读取 DATABASE_URL（与业务代码共用同一个连接串来源）；
- 加载 app.data.models 的 metadata 以支持 autogenerate；
- 使用异步引擎（asyncpg）跑迁移。
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import settings
from app.data.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用业务配置的连接串覆盖 alembic.ini 里的占位（保持单一数据源）
config.set_main_option('sqlalchemy.url', settings.database_url)

target_metadata = Base.metadata

# checkpoint_* 表由 langgraph（AsyncPostgresSaver.setup）运行时创建，不在 Base.metadata 里，
# autogenerate 会把它们误判为「应删除」。这里显式排除，避免迁移脚本生成 drop_table。
EXCLUDE_TABLES = {'checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations'}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == 'table' and name in EXCLUDE_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """离线模式：只输出 SQL 不连库（`alembic upgrade head --sql`）。"""
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
