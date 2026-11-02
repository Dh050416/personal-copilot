"""用户认证与鉴权：账号密码登录 + 基于签名的用户令牌。

设计要点（个人多用户部署，无外部依赖）：
- 密码用 hashlib.pbkdf2_hmac + 随机盐哈希存储（不存明文）。
- 登录成功签发令牌：`<user_id>.<signature>`，签名 = HMAC(SECRET, user_id)。
  SECRET 来自环境变量 AUTH_SECRET；未设置时自动生成并落盘到 data/auth_secret，
  保证容器重启后已签发的令牌仍然有效。
- 令牌本身不自含过期时间（个人部署，长期登录体验优先），但签名的 user_id
  必须能对到真实存在的用户，否则拒绝。
- 鉴权中间件把解析出的 user_id 挂到 request.state.user_id，下游所有数据读写
  都按它隔离。
"""
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_PUBLIC_PATHS = {'/health', '/docs', '/openapi.json', '/redoc'}
_AUTH_PATHS = {'/api/auth/register', '/api/auth/login'}

_ITERATIONS = 200_000


def _secret_file(data_dir: Path) -> Path:
    return data_dir / 'auth_secret'


def load_or_create_secret(data_dir: Path) -> str:
    """读取或创建签名密钥：优先环境变量，否则从 data/auth_secret 读取/生成。"""
    env = os.environ.get('AUTH_SECRET', '').strip()
    if env:
        return env
    path = _secret_file(data_dir)
    if path.exists():
        return path.read_text(encoding='utf-8').strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    path.write_text(secret, encoding='utf-8')
    return secret


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希：`pbkdf2$<iterations>$<salt_hex>$<hash_hex>`。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _ITERATIONS)
    return f'pbkdf2${_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split('$')
        if scheme != 'pbkdf2':
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(iterations))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def sign_token(user_id: int, secret: str) -> str:
    sig = hmac.new(secret.encode('utf-8'), str(user_id).encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{user_id}.{sig}'


def verify_token(token: str, secret: str) -> int | None:
    """校验令牌，返回 user_id；非法返回 None。"""
    try:
        raw_id, sig = token.split('.', 1)
        user_id = int(raw_id)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret.encode('utf-8'), str(user_id).encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


class AuthMiddleware(BaseHTTPMiddleware):
    """鉴权：从请求头解析用户令牌，把 user_id 挂到 request.state；未认证则 401。

    放行 /health 与认证端点（注册/登录）。user_id 会随 Request 透传到所有
    API/服务层，作为数据隔离的唯一依据。
    """

    def __init__(self, app, secret: str):
        super().__init__(app)
        self._secret = secret

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path in _AUTH_PATHS:
            return await call_next(request)
        auth = request.headers.get('authorization', '')
        token = request.headers.get('x-access-token', '')
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
        user_id = verify_token(token, self._secret) if token else None
        if user_id is None:
            return JSONResponse({'detail': '未登录或登录已失效，请重新登录'}, status_code=401)
        request.state.user_id = user_id
        request.state.token = token
        return await call_next(request)
