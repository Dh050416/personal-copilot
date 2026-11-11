"""本地/开发启动入口。

Windows 下 psycopg（checkpoint-postgres 驱动）异步模式不支持 ProactorEventLoop，
而 uvicorn 在 Windows 硬编码返回 ProactorEventLoop（uvicorn.loops.asyncio.asyncio_loop_factory）。
这里通过 monkeypatch 让 uvicorn 的 asyncio loop 走 SelectorEventLoop，再启动服务。

生产环境（Docker/Linux）默认就是 SelectorEventLoop，直接 `uvicorn app.main:app` 即可，
无需本入口。
"""
import asyncio
import sys

import uvicorn
import uvicorn.loops.asyncio as uvicorn_asyncio


def _selector_loop_factory(use_subprocess: bool = False):
    # 忽略 use_subprocess，统一返回 SelectorEventLoop（Windows 下 psycopg 兼容）
    return asyncio.SelectorEventLoop


if sys.platform == 'win32':
    uvicorn_asyncio.asyncio_loop_factory = _selector_loop_factory

if __name__ == '__main__':
    # log_config=None：不让 uvicorn 用默认 dictConfig 覆盖 app.main 装好的 JSON 日志
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000, reload=True,
                log_config=None)
