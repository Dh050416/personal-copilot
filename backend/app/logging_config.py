"""结构化日志与 request_id 上下文（可观测性的地基）。

设计要点：
- request_id 存在 ContextVar 而不是全局变量：async 并发下每条协程链读到自己的值，
  BaseHTTPMiddleware / asyncio.create_task 派生的子任务也会 copy 到该上下文。
- 日志统一输出单行 JSON，字段固定，便于 grep、采集，以及按 request_id 聚合一次
  请求的全部日志（入口 -> LangGraph -> 工具 -> LLM -> 异常）。
- setup_logging() 幂等，且只移除自己安装的 handler，不动 root 上其他 handler
  （pytest caplog 等第三方捕获器不能被我们清掉）。
- uvicorn 启动时会用 dictConfig 覆盖 logging，因此 lifespan 里会再调用一次本函数。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# '-' 表示「不在请求上下文里」（启动脚本、后台任务、定时任务）
REQUEST_ID: ContextVar[str] = ContextVar('request_id', default='-')

# LogRecord 的内置属性：用于把调用方 extra={...} 的自定义字段单独挑出来
_LOG_RECORD_STANDARD = set(
    logging.LogRecord('', 0, '', 0, '', (), None).__dict__
) | {'message', 'asctime', 'taskName'}
# uvicorn 会往 extra 塞带 ANSI 转义的 color_message，采集 JSON 时必须丢掉
_IGNORED_EXTRAS = {'color_message'}

_own_handler: logging.Handler | None = None


class RequestIdFilter(logging.Filter):
    """把当前协程链的 request_id 注入每条 LogRecord。

    允许调用方用 extra={'request_id': ...} 显式指定：异常处理器、SSE 流等
    可能运行在 ContextVar 已被 reset 的上下文里，显式值优先级更高。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        current = getattr(record, 'request_id', None)
        if not current or current == '-':
            record.request_id = REQUEST_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    """单行 JSON：固定字段 + extra 自定义字段 + 异常堆栈。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            'ts': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'event': record.getMessage(),
            'request_id': getattr(record, 'request_id', '-'),
        }
        for key, value in record.__dict__.items():
            if key not in _LOG_RECORD_STANDARD and key not in _IGNORED_EXTRAS \
                    and key != 'request_id':
                payload[key] = value
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        if record.stack_info:
            payload['stack'] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str | None = None) -> None:
    """接管根 logger 输出 JSON；失败/重复调用都不影响主流程。"""
    global _own_handler
    resolved = (level or os.environ.get('LOG_LEVEL') or 'INFO').upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    if _own_handler is not None and _own_handler in root.handlers:
        root.removeHandler(_own_handler)
    root.addHandler(handler)
    root.setLevel(resolved)
    _own_handler = handler

    # uvicorn 默认给自己的 logger 装了纯文本 handler 且 propagate=False，
    # 清空后统一走 root 的 JSON handler，避免同一条日志输出两种格式。
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True
    # HTTP 客户端库的逐请求 INFO 日志噪音较大，压到 WARNING
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Langfuse 服务不可达时 OTLP exporter 会自行重试并疯狂打 WARNING，
    # 主流程不中断但日志会被刷屏；SDK 有自身重试，这里统一压到 ERROR。
    logging.getLogger('opentelemetry').setLevel(logging.ERROR)
