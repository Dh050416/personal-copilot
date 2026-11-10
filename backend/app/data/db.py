"""业务数据库访问层：基于 SQLAlchemy 异步引擎 + PostgreSQL（asyncpg）。

表结构由 Alembic 迁移管理（backend/alembic/versions），本模块只负责：
- 按 DATABASE_URL 创建异步引擎与 session factory；
- 启动时验证连接可用（建表交给 `alembic upgrade head`，不在此 create_all）。
"""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


class Database:
    def __init__(self, url: str | None = None, *, poolclass=None):
        self.url = url or settings.database_url
        # poolclass 可注入：测试走 NullPool 避免 asyncpg 连接跨事件循环复用（asyncpg 严格绑定创建时的 loop）
        self.engine = create_async_engine(self.url, echo=False, pool_pre_ping=True, poolclass=poolclass)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self):
        """启动探活：执行一条轻量查询确认数据库可达、连接池可用。

        建表由 Alembic 负责（部署/启动前运行 `alembic upgrade head`）。
        """
        async with self.engine.connect() as conn:
            await conn.exec_driver_sql('SELECT 1')

    async def close(self):
        await self.engine.dispose()
