"""A（全局兜底 + request_id + 结构化日志）与 C 的一半（Langfuse trace 关联）回归测试。

纯本地验证：不连数据库、不连 Langfuse 服务。用 mini FastAPI app 挂真实的中间件与
异常处理器，用假 CallbackHandler 验证「每次请求新建 + trace_id 绑定」。
"""
import json
import logging
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.testclient import TestClient

from app import observability
from app.api.chat import stream_graph
from app.logging_config import REQUEST_ID, JsonFormatter, RequestIdFilter
from app.main import http_error_handler, unhandled_error_handler, validation_error_handler
from app.middleware import REQUEST_ID_HEADER, RequestIdMiddleware, get_request_id
from app.observability import langfuse_callbacks


def make_app():
    """挂上生产同款中间件与异常处理器，但不触发项目 lifespan（不连库）。"""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get('/ok')
    async def ok():
        return {'ok': True}

    @app.get('/item/{n}')
    async def item(n: int):
        return {'n': n}

    @app.get('/missing')
    async def missing():
        raise HTTPException(404, '不存在')

    @app.get('/boom')
    async def boom():
        raise RuntimeError('secret-internal-detail')

    return app


def test_request_id_header_unique_and_propagated():
    client = TestClient(make_app())
    r1 = client.get('/ok')
    r2 = client.get('/ok')
    assert r1.status_code == 200
    assert r1.headers[REQUEST_ID_HEADER]
    assert r1.headers[REQUEST_ID_HEADER] != r2.headers[REQUEST_ID_HEADER]
    # 网关/前端传入合法 id 时沿用，便于跨服务串联
    r3 = client.get('/ok', headers={REQUEST_ID_HEADER: 'gateway-abc-123'})
    assert r3.headers[REQUEST_ID_HEADER] == 'gateway-abc-123'
    # 非法字符（空格）不得进日志，重新生成
    r4 = client.get('/ok', headers={REQUEST_ID_HEADER: 'bad id with spaces'})
    assert r4.headers[REQUEST_ID_HEADER] != 'bad id with spaces'


def test_unhandled_error_returns_500_with_request_id_and_no_leak():
    client = TestClient(make_app(), raise_server_exceptions=False)
    resp = client.get('/boom')
    assert resp.status_code == 500
    body = resp.json()
    assert 'secret-internal-detail' not in json.dumps(body, ensure_ascii=False)
    assert body['request_id'] == resp.headers[REQUEST_ID_HEADER]
    assert body['detail'] == '服务器内部错误，请稍后重试'


def test_http_exception_and_validation_error_carry_request_id():
    client = TestClient(make_app())
    r = client.get('/missing')
    assert r.status_code == 404
    assert r.json()['detail'] == '不存在'
    assert r.json()['request_id'] == r.headers[REQUEST_ID_HEADER]
    r2 = client.get('/item/not-an-int')
    assert r2.status_code == 422
    assert r2.json()['request_id'] == r2.headers[REQUEST_ID_HEADER]


def test_json_formatter_injects_request_id_extras_and_stack():
    fmt = JsonFormatter()
    flt = RequestIdFilter()

    rec = logging.LogRecord('app.test', logging.ERROR, __file__, 1,
                            'failed %s', ('tool',), None)
    rec.tool = 'search_knowledge'
    flt.filter(rec)
    out = json.loads(fmt.format(rec))
    assert out['event'] == 'failed tool'
    assert out['level'] == 'ERROR'
    assert out['request_id'] == '-'          # 不在请求上下文
    assert out['tool'] == 'search_knowledge'

    token = REQUEST_ID.set('rid-xyz')
    try:
        rec2 = logging.LogRecord('app.test', logging.INFO, __file__, 2,
                                 'in request', (), None)
        flt.filter(rec2)
        assert json.loads(fmt.format(rec2))['request_id'] == 'rid-xyz'
    finally:
        REQUEST_ID.reset(token)

    rec3 = logging.LogRecord('app.test', logging.INFO, __file__, 3,
                             'explicit', (), None)
    rec3.request_id = 'explicit-rid'
    flt.filter(rec3)
    assert json.loads(fmt.format(rec3))['request_id'] == 'explicit-rid'

    try:
        raise ValueError('boom-stack')
    except ValueError:
        rec4 = logging.LogRecord('app.test', logging.ERROR, __file__, 4,
                                 'crashed', (), sys.exc_info())
    flt.filter(rec4)
    assert 'ValueError: boom-stack' in json.loads(fmt.format(rec4))['exception']


def test_langfuse_callbacks_disabled_when_not_configured(monkeypatch):
    monkeypatch.setattr(observability, '_enabled', False)
    monkeypatch.setattr(observability, '_client', None)
    assert langfuse_callbacks('rid-1') == []


def test_langfuse_callbacks_new_handler_per_request_bound_to_trace_id(monkeypatch):
    import langfuse.langchain as lf_langchain

    created = []

    class FakeHandler:
        def __init__(self, *, trace_context=None):
            self.trace_context = trace_context
            created.append(self)

    monkeypatch.setattr(lf_langchain, 'CallbackHandler', FakeHandler)
    monkeypatch.setattr(observability, '_enabled', True)
    monkeypatch.setattr(observability, '_client', object())

    a = langfuse_callbacks('rid-a')
    b = langfuse_callbacks('rid-b')
    assert len(a) == len(b) == 1
    assert a[0] is not b[0]                       # 复用单例会导致并发 trace 串台
    assert a[0].trace_context == {'trace_id': 'rid-a'}
    assert b[0].trace_context == {'trace_id': 'rid-b'}
    assert langfuse_callbacks(None)[0].trace_context is None


def test_langfuse_handler_failure_degrades_to_no_tracing(monkeypatch):
    import langfuse.langchain as lf_langchain

    def broken(*args, **kwargs):
        raise RuntimeError('langfuse down')

    monkeypatch.setattr(lf_langchain, 'CallbackHandler', broken)
    monkeypatch.setattr(observability, '_enabled', True)
    monkeypatch.setattr(observability, '_client', object())
    assert langfuse_callbacks('rid-1') == []


def test_sse_error_event_carries_request_id():
    """流式响应已开始后抛异常：全局 handler 改不了响应，必须靠 SSE error 事件带 rid。"""

    class BoomGraph:
        def astream(self, *args, **kwargs):
            async def gen():
                raise RuntimeError('stream exploded')
                yield  # pragma: no cover - 使之成为 async generator

            return gen()

    mini = FastAPI()
    mini.add_middleware(RequestIdMiddleware)

    @mini.post('/stream')
    async def stream(request: Request):
        return stream_graph(BoomGraph(), {'messages': []}, 'u1:sess', None,
                            request_id=get_request_id(request))

    with TestClient(mini).stream('POST', '/stream') as resp:
        rid = resp.headers[REQUEST_ID_HEADER]
        lines = list(resp.iter_lines())
    events = [json.loads(line[6:]) for line in lines
              if line.startswith('data: ') and line[6:] != '[DONE]']
    err = next(e for e in events if e['type'] == 'error')
    assert err['payload']['code'] == 'RUN_FAILED'
    assert err['payload']['request_id'] == rid
