"""认证端点：注册 / 登录 / 当前用户信息。

注册：用户名 + 密码，用户名唯一，密码 PBKDF2 哈希落库。
登录：校验密码，成功签发用户令牌（user_id 签名），返回 {token, user}。
"""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.auth import hash_password, sign_token, verify_password
from app.data.models import User
from app.schemas import AuthResponse, LoginRequest, RegisterRequest, UserView

router = APIRouter(prefix='/api/auth', tags=['auth'])

_MIN_PASSWORD_LEN = 6
_MAX_USERNAME_LEN = 64


def _user_view(user: User) -> UserView:
    return UserView(id=user.id, username=user.username)


@router.post('/register', response_model=AuthResponse)
async def register(payload: RegisterRequest, request: Request):
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, '用户名不能为空')
    if len(username) > _MAX_USERNAME_LEN:
        raise HTTPException(400, '用户名过长')
    if len(payload.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(400, f'密码至少 {_MIN_PASSWORD_LEN} 位')
    async with request.app.state.db.sessions() as session:
        async with session.begin():
            existing = await session.scalar(select(User).where(User.username == username))
            if existing is not None:
                raise HTTPException(409, '该用户名已被注册，请换一个')
            user = User(username=username, password_hash=hash_password(payload.password))
            session.add(user)
            await session.flush()
            token = sign_token(user.id, request.app.state.auth_secret)
            result_user = _user_view(user)
    return AuthResponse(token=token, user=result_user)


@router.post('/login', response_model=AuthResponse)
async def login(payload: LoginRequest, request: Request):
    username = payload.username.strip()
    async with request.app.state.db.sessions() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(401, '用户名或密码错误')
        token = sign_token(user.id, request.app.state.auth_secret)
        result_user = _user_view(user)
    return AuthResponse(token=token, user=result_user)


@router.get('/me', response_model=UserView)
async def me(request: Request):
    """返回当前登录用户信息（鉴权中间件已挂 user_id）。"""
    user_id = getattr(request.state, 'user_id', None)
    if user_id is None:
        raise HTTPException(401, '未登录')
    async with request.app.state.db.sessions() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(401, '用户不存在')
        return _user_view(user)
