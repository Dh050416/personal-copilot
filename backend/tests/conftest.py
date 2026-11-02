"""pytest 全局 fixture：多用户隔离改造后，测试统一使用固定的 user_id 与合法令牌。

测试不涉及多用户隔离验证（隔离已由端到端用例覆盖），这里：
1. 把 _USER_ID ContextVar 固定为 1，使工具层 current_user_id() 返回 1，
   与各测试 seed 数据时写入的 user_id=1 对齐。
2. 提供 auth_headers fixture：用与 main.py 相同的 secret 签发合法令牌，
   使走 HTTP 接口（/api/chat 等）的测试无需真实注册即可通过鉴权中间件。
"""
import pytest

from app.agent.tools import _USER_ID
from app.auth import load_or_create_secret, sign_token
from app.config import Settings

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
