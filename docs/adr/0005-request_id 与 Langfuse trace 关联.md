# ADR 0005：request_id 贯穿请求链路，并直接作为 Langfuse trace_id

- 状态：已接受
- 日期：2026-11-11
- 相关：`backend/app/logging_config.py`、`app/middleware.py`、`app/main.py`、
  `app/observability.py`、`app/api/chat.py`、`frontend/src/stores/session.ts`

## 背景

一次 `POST /api/chat` 会穿过 AuthMiddleware → chat 端点 → LangGraph → 模型 →
工具 / PG / Redis → SSE 流。并发请求下日志互相交错，出了问题无法回答
「哪几行日志属于这次失败」，也无从把后端日志和用户看到的报错对应起来。

改动前的三个具体缺口：

- `api/chat.py` 的 `except Exception` 只把 `str(exc)` 塞进 SSE `error` 事件，
  既没有日志也没有任何标识；
- `observability.py` 把 `_handler = CallbackHandler()` 缓存成模块级单例，
  而 Langfuse v3 的 handler 在**构造时**固化 trace 归属，并发请求的 span 会串到同一条 trace；
- 项目已有的 `session_id` / `thread_id` / `run_id` 都是**会话级**（`u{user_id}:{session_id}`），
  缺少「一次 HTTP 请求」这个粒度，无法区分同一窗口里的第几轮。

## 决策

用一个字符串把日志、错误响应、Langfuse 三方串起来：

1. **生成**：纯 ASGI `RequestIdMiddleware` 注册在最外层，入口生成 `uuid4().hex`
   （或校验并透传网关的 `X-Request-ID`），写入 `ContextVar`、`scope['state']`、
   响应头 `X-Request-ID`；同时记录 `method / path / status / duration_ms` 访问日志。
2. **传递**：`RequestIdFilter` 把 rid 注入每条 `LogRecord`，`JsonFormatter` 输出单行 JSON；
   调用方可用 `extra={'request_id': ...}` 显式覆盖。
3. **兜底**：`main.py` 注册 `HTTPException` / `RequestValidationError` / `Exception`
   三个处理器，统一返回带 rid 的 JSON，内部错误细节只进日志。
4. **流式路径**：SSE 已开始推送后异常处理器改不了响应，改由中间件记录带 rid 的堆栈，
   `error` 事件携带 rid，前端在错误提示里显示「问题编号」。
5. **Langfuse**：每次请求**新建** `CallbackHandler(trace_context={'trace_id': request_id})`；
   再用 `config.metadata['langfuse_session_id'] = thread_id` 把同会话多轮请求
   （含审批 resume）归到同一个 session。未配置凭证或构造失败一律降级为空回调列表。

## 理由

1. **一个 id 胜过一张映射表**。日志、错误响应、trace 共用同一个字符串，
   用户报一个编号就能 `grep` 出完整链路，不需要维护「rid ↔ trace_id」的对应关系。
2. **ContextVar 而不是全局变量**。async 并发下每条协程链读到自己的值；
   流入口显式 `set/reset` 让 `asyncio.create_task` 派生的 runner 也继承到 rid。
3. **显式 extra 与 `scope['state']` 是必要的双保险**。未捕获异常由
   `ServerErrorMiddleware` 处理，它位于本中间件**外层**，此时 `finally` 已把 ContextVar
   reset 掉，只能靠 `request.state` 取回 rid；因此 `get_request_id()` 先查 state。
   同理 async handler 里用 `logger.error(..., exc_info=exc)` 而不是 `logger.exception()`。
4. **最外层纯 ASGI**。OPTIONS 预检和鉴权 401 到不了端点，也必须带 rid；
   `BaseHTTPMiddleware` 会把响应体包进额外 task，流式的结束时机与 ContextVar 传播更难保证。
5. **trace_id 直接复用 rid，session_id 用 thread_id**。前者精确到「这一轮」，
   后者补上「整个聊天窗口」，两个粒度在 Langfuse 里都能查，且不必新造编号。
6. **handler 不能复用**。v3 的 `trace_context` 是构造参数，单例等于把所有并发请求
   挂到同一条 trace 上——这不是性能取舍，是正确性 bug。

## 代价与已否决的替代方案

**代价**：

- 每次请求新建 handler 有轻微构造开销（相对一次模型调用可忽略）。Langfuse 服务不可达时
  SDK 会在后台重试并打 WARNING，故把 `opentelemetry` logger 压到 ERROR 避免刷屏，
  主流程不受影响。
- `request_id` 是请求级：审批 resume 是另一次 HTTP 请求，会拿到新 rid；
  跨请求归组靠 `langfuse_session_id`，不能指望 rid 串起来。
- rid 只解决「**能定位**」，不解决「早发现」（告警 / 健康检查，即 B）；
  「不崩」靠的是异常兜底本身，rid 只是把兜底现场记下来。
- 结构化日志含 `path` / `status` / 异常堆栈，异常文本可能带工具参数，需注意日志访问权限。

**已否决的替代方案**：

- *复用全局单例 handler，事后用 metadata 改 trace 归属*：v3 在构造时固化归属，改不了；
  并发一定串台。
- *直接用 `thread_id` 当 trace_id*：一个会话所有轮次会挤进一条 trace，
  无法按轮次排查，也和 Langfuse 的 session 概念重复。
- *另造 OpenTelemetry trace_id，再维护 rid ↔ trace_id 映射*：多一层状态与一张表，
  个人单实例项目收益不足。
- *用 `BaseHTTPMiddleware` 实现*：流式响应体的 task 边界会让 ContextVar 的 reset 时机
  与访问日志的结束时机不可靠。
- *把异常详情直接返回前端方便调试*：会泄露内部路径与参数；改为只回通用提示 + rid，
  完整堆栈留在日志里。

## 验证

`backend/tests/test_observability.py`（8 个用例，不连库、不连 Langfuse 服务）：

- rid 唯一性、合法头透传、非法头拒绝、401 / 404 / 422 / 500 响应均带 rid 且不泄露异常详情
- JSON 日志包含 rid、extra 自定义字段与异常堆栈；不在请求上下文时为 `-`
- 每次调用返回**新的** handler 且 `trace_context == {'trace_id': ...}`；
  未启用或构造失败时降级为 `[]`
- SSE 流中途抛错时 `error` 事件带 rid，且与响应头一致

全量回归 208 个用例通过。另在 8011（`log_config=None`）与 8012（Docker 默认的
uvicorn `dictConfig`）两种启动方式下实测：`/health`、401、OPTIONS 预检均带 rid，
日志为单行 JSON 且 `uvicorn.access` 也带同一 rid。
