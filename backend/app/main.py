import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_openai import ChatOpenAI

# Windows 下 psycopg（checkpoint-postgres 的驱动）异步模式不支持 ProactorEventLoop，
# 必须用 SelectorEventLoop。这里在导入期切 policy，覆盖脚本/测试等非 uvicorn 入口；
# uvicorn 启动（Windows 本地开发）需用 run.py（monkeypatch 其硬编码的 Proactor），
# 生产 Docker/Linux 默认即 SelectorEventLoop，无需处理。
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from app.agent.graph import ToolResultCache, make_graph
from app.agent.tools import CACHEABLE_TOOLS, OUTPUT_SCHEMAS, WRITE_TOOLS, build_tools
from app.api.auth import router as auth_router
from app.api.bills import router as bills_router
from app.api.cards import router as cards_router
from app.api.chat import router as chat_router
from app.api.events import router as events_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.reports import router as reports_router
from app.auth import AuthMiddleware, load_or_create_secret
from app.config import Settings
from app.data.db import Database
from app.data.models import AgentMemory
from app.logging_config import setup_logging
from app.middleware import REQUEST_ID_HEADER, RequestIdMiddleware, get_request_id
from app.observability import flush as langfuse_flush
from app.observability import init_langfuse

# 应用导入期就装好 JSON 日志（脚本 / pytest 入口不经过 uvicorn）。
# uvicorn 启动时会用 dictConfig 覆盖 logging，故 lifespan 里再装一次，保证两种入口一致。
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()  # 覆盖 uvicorn 的默认纯文本日志配置
    settings = Settings()
    init_langfuse(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    await db.initialize()
    # 用户令牌签名密钥：环境变量或 data/auth_secret（容器重启后令牌仍有效）
    auth_secret = load_or_create_secret(settings.data_dir)
    checkpoint_cm = AsyncPostgresSaver.from_conn_string(settings.checkpoint_url)
    checkpointer = await checkpoint_cm.__aenter__()
    await checkpointer.setup()
    model = ChatOpenAI(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model=settings.model_name,
        temperature=0,
        max_tokens=settings.model_output_tokens,
        timeout=settings.model_timeout_seconds,
        max_retries=2,
    )

    async def load_memory_text(user_id: int) -> str | None:
        """长期记忆全量加载（agent_memory 表 → 注入文本），按 user_id 隔离。
        规模小（10-30 条），全量优于检索。"""
        async with db.sessions() as session:
            rows = list(await session.scalars(
                select(AgentMemory).where(AgentMemory.user_id == user_id)
                .order_by(AgentMemory.updated_at.desc())))
        if not rows:
            return None
        return '；'.join(f'{r.key}={r.value}' for r in rows)

    tool_cache = None
    redis_client = None
    if settings.tool_result_cache_ttl:
        if settings.redis_url:
            from redis.asyncio import Redis

            from app.cache import RedisToolResultCache
            redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
            tool_cache = RedisToolResultCache(redis_client, settings.tool_result_cache_ttl)
        else:
            tool_cache = ToolResultCache(settings.tool_result_cache_ttl)
    # RAG 知识库 embedding client（本地 ollama bge-m3，与 chat 模型独立配置）
    # 必须在 build_tools 之前创建：search_knowledge 工具依赖它做 query 向量化
    from app.services.knowledge.embedding import EmbeddingClient
    embedding_client = EmbeddingClient(
        base_url=settings.embedding_base_url, api_key=settings.embedding_api_key,
        model=settings.embedding_model_name, batch_size=settings.embedding_batch_size)
    graph = make_graph(model, build_tools(db, embedding_client), WRITE_TOOLS, checkpointer,
                       memory_loader=load_memory_text,
                       tool_timeout=settings.tool_timeout_seconds,
                       model_retries=settings.model_retries,
                       retry_backoff=settings.retry_backoff_seconds,
                       tool_retries=settings.tool_retries,
                       output_schemas=OUTPUT_SCHEMAS,
                       context_max_chars=settings.context_max_chars,
                       context_max_lines=settings.context_max_lines,
                       tool_cache=tool_cache,
                       cacheable_tools=CACHEABLE_TOOLS,
                       sensitive_terms=settings.sensitive_terms)
    app.state.settings = settings
    app.state.db = db
    app.state.auth_secret = auth_secret
    app.state.checkpointer = checkpointer
    app.state.graph = graph
    app.state.tool_cache = tool_cache
    app.state.embedding_client = embedding_client
    # 对话摘要（递进摘要/滑动窗口）：在续轮历史裁剪时增量合并被挤出的消息
    from app.services.summary import SummaryManager
    app.state.summary_manager = SummaryManager(db, max_chars=settings.summary_max_chars)
    # 线程运行时状态：thread_id → {active, pending(追加输入队列), waiting_approval}
    # 检查点注入（执行中可追加任务）的队列宿主；单进程内存即可（本机单实例）
    app.state.thread_runtime = {}
    try:
        yield
    finally:
        langfuse_flush()
        await checkpoint_cm.__aexit__(None, None, None)
        await db.close()
        if redis_client is not None:
            await redis_client.aclose()


app = FastAPI(title='PersonalCopilot', lifespan=lifespan)
# CORS 白名单在应用创建时就要确定（此时 lifespan 尚未运行），单独读一次配置。
# 部署到非默认端口/域名时设置环境变量 CORS_ORIGINS（JSON 数组）。
_boot_settings = Settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_boot_settings.cors_origins,
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
    allow_headers=['*'],
)
# 访问鉴权：按用户令牌（user_id 签名）隔离数据。注册/登录/健康检查放行。
app.add_middleware(AuthMiddleware, secret=load_or_create_secret(_boot_settings.data_dir))
# 最外层（最后注册）：让 OPTIONS 预检、401、流式响应异常也都带上 request_id
app.add_middleware(RequestIdMiddleware)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(bills_router)
app.include_router(cards_router)
app.include_router(events_router)
app.include_router(memory_router)
app.include_router(reports_router)
app.include_router(knowledge_router)


@app.get('/health')
async def health():
    return {'status': 'ok', 'model_provider': app.state.settings.model_provider}


# ----- 全局异常兜底（A）：任何未处理异常都转成带 request_id 的 JSON，进程不崩 -----
# 三者都只把 rid + 可读提示返回前端，完整堆栈留在结构化日志里，避免泄露内部信息。

@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    """业务异常：保留原 status/detail 契约，补上 request_id。"""
    rid = get_request_id(request)
    log = logger.warning if exc.status_code >= 500 else logger.info
    log('request.http_error', extra={
        'request_id': rid, 'method': request.method, 'path': request.url.path,
        'status': exc.status_code, 'detail': str(exc.detail),
    })
    return JSONResponse({'detail': exc.detail, 'request_id': rid},
                        status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    rid = get_request_id(request)
    logger.info('request.validation_error', extra={
        'request_id': rid, 'method': request.method, 'path': request.url.path,
        'errors': len(exc.errors()),
    })
    return JSONResponse({'detail': jsonable_encoder(exc.errors()), 'request_id': rid},
                        status_code=422, headers={REQUEST_ID_HEADER: rid})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """最后的兜底。ServerErrorMiddleware 位于 RequestIdMiddleware 外层，
    它生成的响应不会经过我们的 send 包装，所以这里显式补 X-Request-ID 响应头。

    注意：StreamingResponse 一旦开始推送，异常处理器已无法改写响应；
    那条路径由中间件记录带 rid 的堆栈，并由 SSE error 事件把 rid 推给前端。
    """
    rid = get_request_id(request)
    # 显式传 exc_info=exc：async handler 被调用时 except 块可能已退出，
    # logger.exception() 依赖的 sys.exc_info() 未必还在。
    logger.error('request.unhandled', exc_info=exc, extra={
        'request_id': rid, 'method': request.method, 'path': request.url.path,
        'error_type': type(exc).__name__,
    })
    return JSONResponse(
        {'detail': '服务器内部错误，请稍后重试', 'request_id': rid},
        status_code=500,
        headers={REQUEST_ID_HEADER: rid},
    )
