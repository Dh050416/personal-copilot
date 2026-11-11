"""纯 ASGI 中间件：生成/透传 request_id，并记录结构化访问日志。

必须注册在最外层（main.py 里最后一个 add_middleware），才能覆盖：
- OPTIONS 预检与鉴权 401（这些请求在 AuthMiddleware 就被拦下，走不到端点）；
- StreamingResponse 已经发出响应头之后的异常（全局异常处理器无法再改响应，
  只能靠中间件记录日志、靠 SSE error 事件把 rid 推给前端）。

用纯 ASGI 而不是 BaseHTTPMiddleware：后者会把响应体包进额外 task，
ContextVar 的传播与流式结束时机都更难保证。
"""
from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

from app.logging_config import REQUEST_ID

logger = logging.getLogger('app.request')

REQUEST_ID_HEADER = 'X-Request-ID'
_HEADER_BYTES = b'x-request-id'
# 允许网关/前端传入已有 rid 以串联调用链，但严格限制字符集与长度，防止日志注入
_RID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def _rid_from_headers(headers) -> str | None:
    for key, value in headers:
        if key.lower() == _HEADER_BYTES:
            candidate = value.decode('latin-1').strip()
            if _RID_RE.fullmatch(candidate):
                return candidate
    return None


def get_request_id(request) -> str:
    """端点 / 异常处理器取 rid：优先 request.state，其次 ContextVar。

    未捕获异常由 ServerErrorMiddleware 处理，它位于本中间件外层，届时
    ContextVar 已在 finally 里 reset，但 scope['state'] 仍在，故 state 优先。
    """
    rid = getattr(request.state, 'request_id', None)
    if rid:
        return rid
    return REQUEST_ID.get()


class RequestIdMiddleware:
    """每个 HTTP 请求绑定一个 rid，随响应头返回，并串联全部日志。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        rid = _rid_from_headers(scope.get('headers', [])) or uuid4().hex
        token = REQUEST_ID.set(rid)
        scope.setdefault('state', {})['request_id'] = rid
        method = scope.get('method', '')
        path = scope.get('path', '')
        started = time.perf_counter()
        status = None

        async def send_with_header(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message.get('status')
                headers = message.setdefault('headers', [])
                if not any(k.lower() == _HEADER_BYTES for k, _ in headers):
                    headers.append((_HEADER_BYTES, rid.encode('latin-1')))
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            # 已开始流式响应时 Starlette 无法再生成错误响应，这里必须留下带 rid 的堆栈
            logger.exception('request.unhandled', extra={
                'request_id': rid, 'method': method, 'path': path, 'status': status,
            })
            raise
        else:
            logger.info('request.finished', extra={
                'request_id': rid, 'method': method, 'path': path, 'status': status,
                'duration_ms': round((time.perf_counter() - started) * 1000, 1),
            })
        finally:
            REQUEST_ID.reset(token)
