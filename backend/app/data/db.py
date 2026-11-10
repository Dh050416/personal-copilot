"""业务数据库访问层：基于 SQLAlchemy 异步引擎。

表结构由 Alembic 迁移管理（backend/alembic/versions），本模块只负责创建引擎与 session factory，
并做启动探活。建表交给 `alembic upgrade head`，不在此 create_all。

引擎选择：
- 传 URL（str）→ 直接用该连接串（生产/开发走 PostgreSQL + asyncpg）。
- 传 Path → 指向 SQLite 临时库（测试 / eval 脚本隔离用）。
历史说明：此前仅支持 SQLite（aiosqlite）+ 手写 PRAGMA / ALTER TABLE 迁移，切 PostgreSQL 后
业务主链走 Alembic，PRAGMA 与手写迁移逻辑全部移除。
"""
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings


class Database:
    def __init__(self, url: str | Path | None = None):
        if url is None:
            url = settings.database_url
        if isinstance(url, Path):
            # 测试 / eval 隔离库：本地 SQLite 文件
            url.parent.mkdir(parents=True, exist_ok=True)
            self.url = f'sqlite+aiosqlite:///{url.resolve().as_posix()}'
        else:
            self.url = url
        self.engine = create_async_engine(self.url, echo=False, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self):
        """启动探活：执行一条轻量查询确认数据库可达、连接池可用。

        生产建表由 Alembic 负责（启动前 `alembic upgrade head`）；测试库（SQLite）
        则在这里 create_all 就地建表，保持测试无需 Alembic 的便捷性。
        """
        if self.url.startswith('sqlite'):
            from app.data.models import Base

            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        async with self.engine.connect() as conn:
            await conn.exec_driver_sql('SELECT 1')

    async def close(self):
        await self.engine.dispose()
