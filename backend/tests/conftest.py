"""pytest 全局 fixture：多用户隔离改造后，测试统一使用固定的 user_id 与合法令牌。

测试不涉及多用户隔离验证（隔离已由端到端用例覆盖），这里：
1. 把 _USER_ID ContextVar 固定为 1，使工具层 current_user_id() 返回 1，
   与各测试 seed 数据时写入的 user_id=1 对齐。
2. 提供 auth_headers fixture：用与 main.py 相同的 secret 签发合法令牌，
   使走 HTTP 接口（/api/chat 等）的测试无需真实注册即可通过鉴权中间件。
3. 提供 db fixture：连测试专用 PostgreSQL 库（PersonManager_test），
   每个测试前清空重建全部表（等价于旧 SQLite 临时库的隔离效果）。
"""
import pytest

from app.agent.tools import _USER_ID
from app.auth import load_or_create_secret, sign_token
from app.config import Settings
from app.data.db import Database
from app.data.models import Base
from sqlalchemy.pool import NullPool

# 测试统一使用的用户 id，seed 数据与工具查询都指向它。
TEST_USER_ID = 1


@pytest.fixture(autouse=True)
def _set_user_id():
    token = _USER_ID.set(TEST_USER_ID)
    yield
    _USER_ID.reset(token)


@pytest.fixture
def auth_headers():
    """返回带合法用户令牌的请求头，供走 HTTP 接口的测试使用。"""
    # 与 app/main.py 模块级中间件使用同一份 secret（环境变量或 data/auth_secret）。
    secret = load_or_create_secret(Settings().data_dir)
    token = sign_token(TEST_USER_ID, secret)
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
async def db():
    """测试专用数据库连接：每个测试前清空重建全部表，保证隔离。

    用 NullPool：asyncpg 连接严格绑定创建时的事件循环，而走 HTTP（TestClient）的测试
    会在与 pytest-asyncio 不同的 loop 上执行工具，池化连接会跨 loop 复用而崩溃。
    NullPool 让每次 sessions() 都新建连接，规避该问题。
    """
    database = Database(Settings().test_database_url, poolclass=NullPool)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()
